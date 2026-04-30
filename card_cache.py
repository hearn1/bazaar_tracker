"""
card_cache.py - Fetches and caches card/item data from data.playthebazaar.com/static.

Run standalone to refresh the cache:
    python card_cache.py

The game downloads these files on startup (visible in Player.log):
    cards, monsters, challenges, gamemodes, levelups, tooltips
"""

import json
from pathlib import Path

import requests

import app_paths
import content_manifest
import db

BASE_URL = "https://data.playthebazaar.com/static"

# All known static data files the game fetches
STATIC_ENDPOINTS = [
    "cards",
    "monsters",
    "challenges",
    "gamemodes",
    "levelups",
    "tooltips",
]

CACHE_DIR = app_paths.static_cache_dir()
_template_name_cache: dict[str, str] = {}
_template_name_cache_loaded = False


def is_suspicious_template_id(template_id: str) -> bool:
    """Flag known bad/sentinel template IDs coming out of live capture."""
    if not template_id:
        return False
    template_id = str(template_id).lower()
    if template_id == "00000000-0000-0000-0000-000000000000":
        return True
    return template_id.endswith("-0000-0000-000000000000")


def fetch_static_file(name: str) -> tuple[dict | list, dict] | tuple[None, None]:
    """Try common URL patterns for a static data file."""
    urls_to_try = [
        f"{BASE_URL}/{name}.json",
        f"{BASE_URL}/{name}",
        f"{BASE_URL}/{name}/",
    ]
    for url in urls_to_try:
        try:
            print(f"[CardCache] Fetching {url} ...")
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                print(f"[CardCache] Got {name} ({len(response.content):,} bytes)")
                data = response.json()
                metadata = {
                    "url": url,
                    "content": response.content,
                    "fetched_at": content_manifest.utc_now_iso(),
                }
                return data, metadata
            if response.status_code == 304:
                print(f"[CardCache] {name} not modified")
                return None, None
            print(f"[CardCache] {url} -> {response.status_code}")
        except Exception as exc:
            print(f"[CardCache] Error fetching {url}: {exc}")
    return None, None


def save_raw(name: str, data):
    """Save raw JSON to disk for inspection."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2))
    print(f"[CardCache] Saved raw {name}.json ({path.stat().st_size:,} bytes)")
    return path


def save_raw_to_dir(name: str, data, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def extract_cards_from_data(name: str, data) -> list:
    """
    The static API may return data in various shapes.
    Try to extract a flat list of card-like objects.

    Confirmed structure for cards.json:
        {"5.0.0": [{card}, {card}, ...]}
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        first_val = next(iter(data.values()), None)
        if isinstance(first_val, list):
            return first_val
        for key in ("cards", "items", "data", "monsters", "entries", name):
            if key in data and isinstance(data[key], list):
                return data[key]
        if isinstance(first_val, dict):
            return list(data.values())
    return []


def _current_card_snapshot() -> dict[str, dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT template_id, name, raw_json FROM card_cache").fetchall()
        snapshot: dict[str, dict] = {}
        for row in rows:
            template_id = str(row["template_id"] or "")
            if not template_id:
                continue
            raw_json = row["raw_json"] or "{}"
            try:
                raw_card = json.loads(raw_json)
            except (TypeError, json.JSONDecodeError):
                raw_card = {}
            snapshot[template_id] = {
                "template_id": template_id,
                "name": row["name"] or template_id,
                "fingerprint": content_manifest.card_fingerprint(raw_card),
            }
        return snapshot
    finally:
        conn.close()


def unresolved_template_report(limit: int = 50) -> dict:
    """Report template IDs seen in live rows but missing from card_cache."""
    db.init_db()
    report = {"count": 0, "items": []}
    conn = db.get_conn()
    try:
        queries = [
            (
                "decisions.chosen_template",
                """
                SELECT chosen_template AS template_id, COUNT(*) AS count
                FROM decisions
                LEFT JOIN card_cache ON decisions.chosen_template = card_cache.template_id
                WHERE chosen_template IS NOT NULL
                  AND chosen_template != ''
                  AND card_cache.template_id IS NULL
                GROUP BY chosen_template
                ORDER BY count DESC
                LIMIT ?
                """,
            ),
            (
                "api_cards.template_id",
                """
                SELECT api_cards.template_id AS template_id, COUNT(*) AS count
                FROM api_cards
                LEFT JOIN card_cache ON api_cards.template_id = card_cache.template_id
                WHERE api_cards.template_id IS NOT NULL
                  AND api_cards.template_id != ''
                  AND card_cache.template_id IS NULL
                GROUP BY api_cards.template_id
                ORDER BY count DESC
                LIMIT ?
                """,
            ),
        ]
        seen = set()
        for source, sql in queries:
            remaining = max(0, limit - len(report["items"]))
            if not remaining:
                break
            for row in conn.execute(sql, (remaining,)).fetchall():
                template_id = row["template_id"]
                if not template_id or template_id in seen or is_suspicious_template_id(template_id):
                    continue
                seen.add(template_id)
                report["items"].append({
                    "template_id": template_id,
                    "count": int(row["count"] or 0),
                    "source": source,
                })
        report["count"] = len(report["items"])
        return report
    finally:
        conn.close()


def card_cache_row_count() -> int:
    try:
        conn = db.get_conn()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM card_cache").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def content_status() -> dict:
    return content_manifest.content_status(
        card_cache_rows=card_cache_row_count(),
        unresolved_report=unresolved_template_report(),
    )


def refresh_cache(*, versioned: bool = False, report_unresolved: bool = True) -> dict:
    """Fetch all static data, populate card_cache, and record update summary."""
    global _template_name_cache_loaded

    db.init_db()
    all_cards = []
    started_at = content_manifest.utc_now_iso()
    previous_manifest = content_manifest.load_manifest()
    previous_endpoint_hashes = content_manifest.endpoint_hashes(previous_manifest)
    previous_card_snapshot = _current_card_snapshot()
    fetched_endpoint_hashes: dict[str, str] = {}
    endpoints_fetched: list[str] = []
    endpoint_warnings: list[str] = []
    versioned_dir = None
    if versioned:
        stamp = started_at.replace(":", "").replace("-", "").replace(".", "_")
        versioned_dir = CACHE_DIR / "versions" / stamp

    for name in STATIC_ENDPOINTS:
        data, fetch_meta = fetch_static_file(name)
        if data is None:
            endpoint_warnings.append(f"{name}: no usable response")
            continue

        if versioned_dir is not None:
            save_raw_to_dir(name, data, versioned_dir)
        raw_path = save_raw(name, data)
        if fetch_meta:
            fetched_endpoint_hashes[name] = content_manifest.sha256_bytes(fetch_meta["content"])
            endpoints_fetched.append(name)
            content_manifest.update_endpoint(
                name,
                url=fetch_meta["url"],
                content=fetch_meta["content"],
                data=data,
                cache_path=raw_path,
                fetched_at=fetch_meta["fetched_at"],
            )

        cards = extract_cards_from_data(name, data)
        if cards:
            print(f"[CardCache] Extracted {len(cards)} entries from {name}")
            all_cards.extend(cards)
        else:
            print(f"[CardCache] Could not extract list from {name} - check static_cache/{name}.json")

    if all_cards:
        db.cache_cards(all_cards)
        _template_name_cache_loaded = False
        print(f"[CardCache] Total cached: {len(all_cards)} entries")
    else:
        print("[CardCache] WARNING: No new card data was cached; keeping the previous local cache active.")

    current_card_snapshot = content_manifest.snapshot_cards(all_cards) if all_cards else previous_card_snapshot
    card_diff = content_manifest.diff_card_snapshots(previous_card_snapshot, current_card_snapshot)
    endpoint_diff = {
        "added": sorted(set(fetched_endpoint_hashes) - set(previous_endpoint_hashes)),
        "removed": [],
        "changed": sorted(
            endpoint
            for endpoint, digest in fetched_endpoint_hashes.items()
            if previous_endpoint_hashes.get(endpoint) and previous_endpoint_hashes.get(endpoint) != digest
        ),
        "unchanged": sorted(
            endpoint
            for endpoint, digest in fetched_endpoint_hashes.items()
            if previous_endpoint_hashes.get(endpoint) == digest
        ),
    }
    unresolved = unresolved_template_report() if report_unresolved else {"count": 0, "items": []}
    status = "ok"
    warnings = []
    if endpoint_warnings:
        status = "warn"
        warnings.extend(endpoint_warnings)
    if not endpoints_fetched:
        status = "warn"
        warnings.append("No static endpoints were refreshed; previous cache remains active.")
    summary = content_manifest.record_refresh_summary(
        started_at=started_at,
        finished_at=content_manifest.utc_now_iso(),
        versioned_cache_dir=versioned_dir,
        endpoints_fetched=endpoints_fetched,
        endpoint_diff=endpoint_diff,
        card_diff=card_diff,
        card_count=len(current_card_snapshot),
        unresolved_report=unresolved,
        status=status,
        warnings=warnings,
    )
    summary["cards"] = all_cards
    return summary


def print_refresh_summary(summary: dict) -> None:
    endpoint_diff = summary.get("endpoint_diff") or {}
    card_diff = summary.get("card_diff") or {}
    unresolved = summary.get("unresolved_template_ids") or {}
    print("[CardCache] Refresh summary:")
    print(f"  Versioned cache: {summary.get('versioned_cache_dir') or '(disabled)'}")
    print(f"  Endpoints fetched: {len(summary.get('endpoints_fetched') or [])}")
    print(
        "  Endpoint diff: "
        f"{len(endpoint_diff.get('added') or [])} added, "
        f"{len(endpoint_diff.get('changed') or [])} changed, "
        f"{len(endpoint_diff.get('unchanged') or [])} unchanged"
    )
    print(
        "  Card diff: "
        f"{card_diff.get('added_count', 0)} added, "
        f"{card_diff.get('removed_count', 0)} removed, "
        f"{card_diff.get('changed_count', 0)} changed"
    )
    if unresolved.get("items"):
        print("  Unresolved template IDs:")
        for item in unresolved["items"][:10]:
            print(f"    {item['template_id']} ({item['count']} row(s), {item['source']})")
    else:
        print("  Unresolved template IDs: none")


def _prime_template_name_cache():
    """Load the full template-name table once for hot lookup paths."""
    global _template_name_cache_loaded
    if _template_name_cache_loaded:
        return

    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT template_id, name FROM card_cache").fetchall()
        _template_name_cache.clear()
        for row in rows:
            template_id = row["template_id"]
            name = row["name"]
            if template_id and name and name != "Unknown":
                _template_name_cache[str(template_id)] = str(name)
    except Exception:
        _template_name_cache.clear()
    finally:
        conn.close()
        _template_name_cache_loaded = True


def resolve_template_id(template_id: str) -> str:
    """
    Return a human-readable name for a template UUID.
    Falls back to a shortened ID if not in cache.
    """
    if not template_id:
        return "Unknown"

    cached = _template_name_cache.get(template_id)
    if cached:
        return cached

    if not _template_name_cache_loaded:
        _prime_template_name_cache()
        cached = _template_name_cache.get(template_id)
        if cached:
            return cached

    card = db.lookup_card(template_id)
    if card and card.get("name") and card["name"] != "Unknown":
        _template_name_cache[template_id] = card["name"]
        return card["name"]

    fallback = f"[{template_id[:8]}...]"
    _template_name_cache[template_id] = fallback
    return fallback


def is_cache_populated() -> bool:
    """Check if the card cache already has data."""
    try:
        conn = db.get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM card_cache").fetchone()[0]
            return count > 100
        finally:
            conn.close()
    except Exception:
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and cache card data from playthebazaar.com")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cache is already populated")
    args = parser.parse_args()

    try:
        db.init_db()

        if not args.force and is_cache_populated():
            conn = db.get_shared_conn()
            count = conn.execute("SELECT COUNT(*) FROM card_cache").fetchone()[0]
            print(f"[CardCache] Cache already populated ({count} entries). Use --force to refresh after a patch.")
        else:
            print("=== Bazaar Card Cache Refresh ===")
            summary = refresh_cache(versioned=True)
            cards = summary.get("cards") or []
            print(f"\nDone. {len(cards)} total entries cached.")
            print(f"Raw JSON files saved to {CACHE_DIR} for inspection.")
            print(f"[CardCache] Content manifest: {content_manifest.summarize_manifest()}")
            print_refresh_summary(summary)
    finally:
        db.close_shared_conn()
