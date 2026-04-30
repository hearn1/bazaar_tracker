import json

import app_paths
import card_cache
import content_manifest
import db


def _point_content_at(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "static_cache"
    monkeypatch.setattr(db, "DB_PATH", data_dir / "bazaar_runs.db")
    monkeypatch.setattr(app_paths, "static_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(card_cache, "STATIC_ENDPOINTS", ["cards"])
    db.close_shared_conn()
    return cache_dir


def test_refresh_content_writes_versioned_cache_and_diff(tmp_path, monkeypatch):
    cache_dir = _point_content_at(tmp_path, monkeypatch)
    calls = {"count": 0}

    first_cards = [
        {"Id": "card-a", "InternalName": "Card A", "Power": 1},
    ]
    second_cards = [
        {"Id": "card-a", "InternalName": "Card A", "Power": 2},
        {"Id": "card-b", "InternalName": "Card B", "Power": 1},
    ]

    def fake_fetch(name):
        calls["count"] += 1
        payload = {"5.0.0": first_cards if calls["count"] == 1 else second_cards}
        content = json.dumps(payload, sort_keys=True).encode("utf-8")
        return payload, {
            "url": f"https://example.invalid/{name}.json",
            "content": content,
            "fetched_at": content_manifest.utc_now_iso(),
        }

    monkeypatch.setattr(card_cache, "fetch_static_file", fake_fetch)

    first = card_cache.refresh_cache(versioned=True, report_unresolved=False)
    second = card_cache.refresh_cache(versioned=True, report_unresolved=False)

    assert first["card_count"] == 1
    assert second["card_count"] == 2
    assert second["card_diff"]["added_count"] == 1
    assert second["card_diff"]["changed_count"] == 1
    assert (cache_dir / "cards.json").exists()
    assert list((cache_dir / "versions").glob("*/cards.json"))

    manifest = content_manifest.load_manifest()
    assert manifest["last_update"]["card_diff"]["added"][0]["template_id"] == "card-b"
    assert manifest["last_update"]["endpoint_diff"]["changed"] == ["cards"]


def test_content_status_includes_manifest_and_unresolved_report(tmp_path, monkeypatch):
    _point_content_at(tmp_path, monkeypatch)
    db.init_db()
    content_manifest.record_refresh_summary(
        started_at="2026-04-30T00:00:00+00:00",
        finished_at="2026-04-30T00:01:00+00:00",
        versioned_cache_dir=None,
        endpoints_fetched=["cards"],
        endpoint_diff={"added": ["cards"], "removed": [], "changed": [], "unchanged": []},
        card_diff={"added_count": 1, "removed_count": 0, "changed_count": 0},
        card_count=1,
        unresolved_report={"count": 1, "items": [{"template_id": "missing", "count": 2, "source": "test"}]},
    )

    status = card_cache.content_status()

    assert status["endpoint_count"] == 0
    assert status["last_update"]["card_count"] == 1
    assert status["unresolved_template_ids"]["count"] == 0


def test_refresh_failure_keeps_previous_cache_and_records_warning(tmp_path, monkeypatch):
    _point_content_at(tmp_path, monkeypatch)
    db.init_db()
    db.cache_cards([{"Id": "card-a", "InternalName": "Card A"}])
    monkeypatch.setattr(card_cache, "fetch_static_file", lambda _name: (None, None))

    summary = card_cache.refresh_cache(versioned=True, report_unresolved=False)

    assert summary["status"] == "warn"
    assert summary["card_count"] == 1
    assert summary["endpoints_fetched"] == []
    assert summary["warnings"]
    assert card_cache.resolve_template_id("card-a") == "Card A"
