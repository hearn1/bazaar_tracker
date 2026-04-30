"""Content manifest helpers for Bazaar Tracker static API cache."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import app_paths

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "content_manifest.json"


def manifest_path() -> Path:
    """Return the centralized static cache content manifest path."""
    return app_paths.static_cache_dir() / MANIFEST_FILENAME


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def detect_game_data_version(data: Any) -> Optional[str]:
    """Best-effort game/static data version detection from endpoint payloads."""
    if isinstance(data, dict):
        version_like_keys = [
            str(key)
            for key, value in data.items()
            if isinstance(key, str)
            and key[:1].isdigit()
            and isinstance(value, (list, dict))
        ]
        if version_like_keys:
            return sorted(version_like_keys)[-1]

        for key in ("version", "Version", "gameVersion", "GameVersion", "dataVersion", "DataVersion"):
            value = data.get(key)
            if isinstance(value, (str, int, float)):
                return str(value)
    return None


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {
            "manifest_version": MANIFEST_VERSION,
            "updated_at": None,
            "detected_game_data_version": None,
            "endpoints": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("content manifest root is not an object")
        data.setdefault("manifest_version", MANIFEST_VERSION)
        data.setdefault("updated_at", None)
        data.setdefault("detected_game_data_version", None)
        data.setdefault("endpoints", {})
        if not isinstance(data["endpoints"], dict):
            data["endpoints"] = {}
        return data
    except Exception as exc:
        return {
            "manifest_version": MANIFEST_VERSION,
            "updated_at": None,
            "detected_game_data_version": None,
            "endpoints": {},
            "load_error": str(exc),
        }


def write_manifest(manifest: dict) -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def endpoint_hashes(manifest: Optional[dict] = None) -> dict[str, str]:
    """Return {endpoint: sha256} for endpoints recorded in a manifest."""
    manifest = manifest if manifest is not None else load_manifest()
    endpoints = manifest.get("endpoints") or {}
    result: dict[str, str] = {}
    for endpoint, record in endpoints.items():
        digest = (record or {}).get("sha256")
        if digest:
            result[str(endpoint)] = str(digest)
    return result


def card_identity(card: dict) -> str:
    return str(
        card.get("Id")
        or card.get("id")
        or card.get("templateId")
        or card.get("TemplateId")
        or ""
    )


def card_display_name(card: dict) -> str:
    return str(
        card.get("InternalName")
        or card.get("internalName")
        or card.get("Name")
        or card.get("name")
        or card.get("Localization", {}).get("Title", {}).get("Text")
        or card_identity(card)
        or "Unknown"
    )


def card_fingerprint(card: dict) -> str:
    payload = json.dumps(card, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def snapshot_cards(cards: list[dict]) -> dict[str, dict]:
    """Return stable card snapshots keyed by template ID."""
    snapshot: dict[str, dict] = {}
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        template_id = card_identity(card)
        if not template_id:
            continue
        snapshot[template_id] = {
            "template_id": template_id,
            "name": card_display_name(card),
            "fingerprint": card_fingerprint(card),
        }
    return snapshot


def diff_card_snapshots(previous: dict[str, dict], current: dict[str, dict]) -> dict:
    """Compute added/removed/changed card summaries between snapshots."""
    previous_ids = set(previous)
    current_ids = set(current)
    added_ids = sorted(current_ids - previous_ids)
    removed_ids = sorted(previous_ids - current_ids)
    changed_ids = sorted(
        template_id
        for template_id in previous_ids & current_ids
        if previous[template_id].get("fingerprint") != current[template_id].get("fingerprint")
    )

    def _entry(snapshot: dict[str, dict], template_id: str) -> dict:
        record = snapshot.get(template_id) or {}
        return {
            "template_id": template_id,
            "name": record.get("name") or template_id,
        }

    return {
        "added_count": len(added_ids),
        "removed_count": len(removed_ids),
        "changed_count": len(changed_ids),
        "added": [_entry(current, template_id) for template_id in added_ids[:50]],
        "removed": [_entry(previous, template_id) for template_id in removed_ids[:50]],
        "changed": [_entry(current, template_id) for template_id in changed_ids[:50]],
    }


def latest_update_summary(manifest: Optional[dict] = None) -> dict:
    manifest = manifest if manifest is not None else load_manifest()
    return manifest.get("last_update") or {}


def content_status(card_cache_rows: int = 0, unresolved_report: Optional[dict] = None) -> dict:
    manifest = load_manifest()
    endpoints = manifest.get("endpoints") or {}
    unresolved_report = unresolved_report or {"items": []}
    return {
        "manifest_path": str(manifest_path()),
        "manifest_version": manifest.get("manifest_version"),
        "updated_at": manifest.get("updated_at"),
        "detected_game_data_version": manifest.get("detected_game_data_version"),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "card_cache_rows": card_cache_rows,
        "last_update": latest_update_summary(manifest),
        "unresolved_template_ids": unresolved_report,
        "summary": summarize_manifest(manifest),
        "load_error": manifest.get("load_error"),
    }


def update_endpoint(
    endpoint: str,
    *,
    url: str,
    content: bytes,
    data: Any,
    cache_path: Path,
    fetched_at: Optional[str] = None,
) -> dict:
    """Record one fetched static endpoint in content_manifest.json."""
    manifest = load_manifest()
    fetched_at = fetched_at or utc_now_iso()
    detected_version = detect_game_data_version(data)

    endpoint_record = {
        "endpoint": endpoint,
        "url": url,
        "fetched_at": fetched_at,
        "size_bytes": len(content),
        "sha256": sha256_bytes(content),
        "cache_path": str(cache_path),
        "detected_game_data_version": detected_version,
    }
    manifest["manifest_version"] = MANIFEST_VERSION
    manifest["updated_at"] = fetched_at
    if detected_version:
        manifest["detected_game_data_version"] = detected_version
    manifest.setdefault("endpoints", {})[endpoint] = endpoint_record
    write_manifest(manifest)
    return endpoint_record


def record_refresh_summary(
    *,
    started_at: str,
    finished_at: str,
    versioned_cache_dir: Optional[Path],
    endpoints_fetched: list[str],
    endpoint_diff: dict,
    card_diff: dict,
    card_count: int,
    unresolved_report: Optional[dict] = None,
    status: str = "ok",
    warnings: Optional[list[str]] = None,
) -> dict:
    """Store the latest refresh-content summary in content_manifest.json."""
    manifest = load_manifest()
    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "versioned_cache_dir": str(versioned_cache_dir) if versioned_cache_dir else None,
        "endpoints_fetched": endpoints_fetched,
        "endpoint_diff": endpoint_diff,
        "card_diff": card_diff,
        "card_count": card_count,
        "unresolved_template_ids": unresolved_report or {"items": []},
        "status": status,
        "warnings": warnings or [],
    }
    manifest["manifest_version"] = MANIFEST_VERSION
    manifest["updated_at"] = finished_at
    manifest["last_update"] = summary
    write_manifest(manifest)
    return summary


def summarize_manifest(manifest: Optional[dict] = None) -> str:
    manifest = manifest if manifest is not None else load_manifest()
    load_error = manifest.get("load_error")
    if load_error:
        return f"unreadable ({load_error})"

    endpoints = manifest.get("endpoints") or {}
    if not endpoints:
        return f"missing/empty at {manifest_path()}"

    version = manifest.get("detected_game_data_version") or "unknown"
    updated_at = manifest.get("updated_at") or "unknown"
    endpoint_parts = []
    for name in sorted(endpoints):
        record = endpoints.get(name) or {}
        size = record.get("size_bytes")
        digest = str(record.get("sha256") or "")[:12]
        if isinstance(size, int):
            endpoint_parts.append(f"{name}:{size:,}B:{digest}")
        else:
            endpoint_parts.append(f"{name}:unknown")
    return (
        f"version={version}; endpoints={len(endpoints)}; updated_at={updated_at}; "
        f"details={', '.join(endpoint_parts)}; path={manifest_path()}"
    )
