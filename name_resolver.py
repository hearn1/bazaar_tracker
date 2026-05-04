"""
name_resolver.py — Centralized name resolution with lazy retry.

Replaces six scattered instance_id → human name code paths with one service:

  resolver.resolve(instance_id, template_id="") -> str
  resolver.bulk_resolve(instance_ids) -> dict[str, str]
  resolver.notify_template(instance_id, template_id)

Resolution chain:
  1. In-memory cache hit (instance_id → name)
  2. template_id supplied or known via notify_template → card_cache lookup
  3. api_cards table (Mono-captured instance→template mapping)
  4. Mark as _unresolved_ for lazy retry on next bulk_resolve call

The resolver is designed to be instantiated once by RunState (for live
resolution during the run) and is also usable from server.py for overlay
rendering.  It does NOT own the card_cache or db modules — it calls into
them as leaf lookups.
"""

import card_cache
import db

# IDs with these prefixes are "raw" runtime instance IDs, not human names.
_RAW_PREFIXES = ("itm_", "enc_", "skl_", "ste_", "com_", "ped_")

# Sentinel stored in cache when resolution fails, so we know to retry.
_UNRESOLVED = object()


def is_unresolved(name: str) -> bool:
    """Return True if `name` looks like a raw instance ID, not a human name."""
    if not name:
        return True
    return (
        name.startswith("[")
        or any(name.startswith(p) for p in _RAW_PREFIXES)
    )


class NameResolver:
    """
    Caches instance_id → human-readable name mappings.

    Resolution is lazy: unresolved IDs are retried on subsequent calls
    to resolve() or bulk_resolve(), which naturally picks them up once
    api_cards rows land from Mono capture.
    """

    def __init__(self, run_id: int | None = None):
        self.run_id = run_id
        # instance_id → resolved human name  OR  _UNRESOLVED sentinel
        self._cache: dict[str, str | object] = {}
        # instance_id → template_id (populated by notify_template and lookups)
        self._template_map: dict[str, str] = {}

    def set_run_id(self, run_id: int | None):
        self.run_id = run_id

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, instance_id: str, template_id: str = "") -> str:
        """
        Resolve a single instance_id to a human name.

        If template_id is provided, it is used directly for the card_cache
        lookup (fast path for purchase events where the log includes the
        template).  Otherwise falls through the full resolution chain.

        Returns the human name, or the raw instance_id if unresolvable.
        """
        if not instance_id:
            return "Unknown"

        # 1. Cache hit — but skip _UNRESOLVED sentinel (triggers retry).
        cached = self._cache.get(instance_id)
        if cached is not None and cached is not _UNRESOLVED:
            return cached

        # 2. Use supplied template_id, or one we already know about.
        tid = template_id or self._template_map.get(instance_id, "")
        if tid:
            name = self._resolve_via_template(instance_id, tid)
            if name:
                return name

        # 3. api_cards lookup (Mono-captured data).
        name = self._resolve_via_api_cards(instance_id)
        if name:
            return name

        # 4. Mark unresolved for lazy retry.
        self._cache[instance_id] = _UNRESOLVED
        return instance_id

    def bulk_resolve(self, instance_ids: list[str]) -> dict[str, str]:
        """
        Resolve a batch of instance IDs.  Returns { instance_id: name }.

        IDs that were previously unresolved are retried — this is the
        "lazy retry" mechanism that picks up api_cards rows as they land.
        """
        result: dict[str, str] = {}
        need_api_lookup: list[str] = []

        for iid in instance_ids:
            if not iid:
                continue

            # Check cache — retry anything marked _UNRESOLVED.
            cached = self._cache.get(iid)
            if cached is not None and cached is not _UNRESOLVED:
                result[iid] = cached
                continue

            # Try template_map first.
            tid = self._template_map.get(iid, "")
            if tid:
                name = self._resolve_via_template(iid, tid)
                if name:
                    result[iid] = name
                    continue

            need_api_lookup.append(iid)

        # Batch api_cards lookup for remaining IDs.
        if need_api_lookup:
            api_map = self._batch_resolve_via_api_cards(need_api_lookup)
            for iid in need_api_lookup:
                if iid in api_map:
                    result[iid] = api_map[iid]
                else:
                    # Still unresolved — mark for future retry.
                    self._cache[iid] = _UNRESOLVED
                    result[iid] = iid  # Return raw ID as fallback.

        return result

    def notify_template(self, instance_id: str, template_id: str):
        """
        Called when Mono capture or bridge learns an instance→template mapping.

        This eagerly resolves the name and caches it so subsequent
        resolve() calls return immediately.
        """
        if not instance_id or not template_id:
            return
        if card_cache.is_suspicious_template_id(template_id):
            return

        self._template_map[instance_id] = template_id
        name = card_cache.resolve_template_id(template_id)
        if name and not is_unresolved(name):
            self._cache[instance_id] = name

    def get_readable_names(self, names: list[str]) -> list[str]:
        """
        Filter a list of names to only those that are human-readable
        (not raw instance IDs).  Deduplicates while preserving order.
        """
        readable = []
        seen = set()
        for name in names or []:
            if not isinstance(name, str) or not name or is_unresolved(name) or name in seen:
                continue
            seen.add(name)
            readable.append(name)
        return readable

    def get_template_id(self, instance_id: str) -> str:
        """Return the known template_id for an instance, or empty string."""
        return self._template_map.get(instance_id, "")

    def clear(self):
        """Clear all caches.  Call on run reset."""
        self._cache.clear()
        self._template_map.clear()

    # ── Internal resolution helpers ───────────────────────────────────────────

    def _resolve_via_template(self, instance_id: str, template_id: str) -> str | None:
        """Resolve using card_cache.  Returns name or None."""
        if card_cache.is_suspicious_template_id(template_id):
            return None
        name = card_cache.resolve_template_id(template_id)
        if name and not is_unresolved(name):
            self._cache[instance_id] = name
            return name
        return None

    def _resolve_via_api_cards(self, instance_id: str) -> str | None:
        """Single-ID api_cards lookup.  Returns name or None."""
        result = self._batch_resolve_via_api_cards([instance_id])
        return result.get(instance_id)

    def _batch_resolve_via_api_cards(self, instance_ids: list[str]) -> dict[str, str]:
        """
        Look up instance_id → human name via the api_cards table.

        This is the Mono-captured data path: api_cards stores
        (instance_id, template_id) pairs from live game state snapshots.
        """
        if not instance_ids:
            return {}

        result: dict[str, str] = {}
        try:
            conn = db.get_conn()
            try:
                for iid in instance_ids:
                    if iid in result:
                        continue
                    rows = conn.execute(
                        """
                        SELECT template_id FROM api_cards
                        WHERE instance_id = ? AND template_id IS NOT NULL AND template_id != ''
                        ORDER BY id DESC
                        """,
                        (iid,),
                    ).fetchall()

                    template_id = ""
                    fallback_tid = ""
                    for row in rows:
                        candidate = row["template_id"]
                        if not candidate:
                            continue
                        if not fallback_tid:
                            fallback_tid = candidate
                        if not card_cache.is_suspicious_template_id(candidate):
                            template_id = candidate
                            break
                    template_id = template_id or fallback_tid

                    if template_id:
                        self._template_map[iid] = template_id
                        name = card_cache.resolve_template_id(template_id)
                        if name and not is_unresolved(name):
                            self._cache[iid] = name
                            result[iid] = name
            finally:
                conn.close()
        except Exception as exc:
            print(f"[NameResolver] _batch_resolve_via_api_cards failed: {exc}")

        return result


# ── Module-level convenience for server.py ────────────────────────────────────

def make_resolver(run_id: int | None = None) -> NameResolver:
    """Create a fresh resolver, typically for a single request cycle."""
    return NameResolver(run_id=run_id)
