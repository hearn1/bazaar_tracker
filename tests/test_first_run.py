import json

import app_paths
import db
import first_run
import settings


def _point_app_at(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings_dir = tmp_path / "settings"
    monkeypatch.setattr(app_paths, "data_dir", lambda: data_dir)
    monkeypatch.setattr(app_paths, "settings_dir", lambda: settings_dir)
    monkeypatch.setattr(app_paths, "settings_path", lambda: settings_dir / "settings.json")
    monkeypatch.setattr(app_paths, "db_path", lambda: data_dir / "bazaar_runs.db")
    monkeypatch.setattr(app_paths, "static_cache_dir", lambda: data_dir / "static_cache")
    monkeypatch.setattr(app_paths, "image_cache_dir", lambda: data_dir / "static_cache" / "images")
    monkeypatch.setattr(db, "DB_PATH", data_dir / "bazaar_runs.db")
    monkeypatch.setattr(settings, "_PATH", None)
    monkeypatch.setattr(settings, "_CACHE", None)
    db.close_shared_conn()


def test_first_run_setup_initializes_and_persists_status(tmp_path, monkeypatch):
    _point_app_at(tmp_path, monkeypatch)
    player_log = tmp_path / "Player.log"
    player_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(first_run, "find_player_log_path", lambda: player_log)
    monkeypatch.setattr(first_run.card_cache, "is_cache_populated", lambda: False)
    monkeypatch.setattr(first_run.refresh_images, "detect_install_roots", lambda: [tmp_path / "install"])
    monkeypatch.setattr(first_run.refresh_images, "detect_cache_roots", lambda: [tmp_path / "cache"])
    monkeypatch.setattr(first_run.refresh_images, "coverage_report", lambda: {"manifest_entries": 0})

    def fake_refresh_content(**_kwargs):
        return {
            "card_count": 2,
            "cards": [{"Id": "a"}, {"Id": "b"}],
            "endpoint_diff": {},
            "card_diff": {},
        }

    report = first_run.run_setup(content_refresh_fn=fake_refresh_content)

    assert report["completed"] is True
    assert (tmp_path / "data" / "bazaar_runs.db").exists()
    status = first_run.setup_status()
    assert status["completed"] is True
    assert status["detected_player_log"] == str(player_log)
    assert status["steps"]["content_cache"]["status"] == "ok"

    saved = json.loads((tmp_path / "settings" / "settings.json").read_text(encoding="utf-8"))
    assert saved["setup"]["completed"] is True


def test_first_run_setup_skips_completed_unless_forced(tmp_path, monkeypatch):
    _point_app_at(tmp_path, monkeypatch)
    settings.load()
    settings.set("setup.completed", True)
    settings.set("setup.steps", {"database": {"status": "ok"}})
    settings.save()

    called = {"refresh": False}

    def fake_refresh_content(**_kwargs):
        called["refresh"] = True
        return {"card_count": 1, "cards": []}

    report = first_run.run_setup(content_refresh_fn=fake_refresh_content)

    assert report["completed"] is True
    assert called["refresh"] is False


def test_content_refresh_failure_is_warning_not_failed_setup(tmp_path, monkeypatch):
    _point_app_at(tmp_path, monkeypatch)
    monkeypatch.setattr(first_run.card_cache, "is_cache_populated", lambda: False)
    monkeypatch.setattr(first_run.refresh_images, "detect_install_roots", lambda: [])
    monkeypatch.setattr(first_run.refresh_images, "detect_cache_roots", lambda: [])
    monkeypatch.setattr(first_run.refresh_images, "coverage_report", lambda: {"manifest_entries": 0})

    def fail_refresh(**_kwargs):
        raise TimeoutError("cdn timed out")

    report = first_run.run_setup(content_refresh_fn=fail_refresh)

    assert report["completed"] is True
    content_step = next(step for step in report["steps"] if step["id"] == "content_cache")
    assert content_step["status"] == "warn"
    assert "cdn timed out" in content_step["message"]
