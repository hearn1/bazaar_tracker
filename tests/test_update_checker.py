import json

import settings
import update_checker


def _reset_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZAAR_TRACKER_SETTINGS_DIR", str(tmp_path))
    settings._CACHE = None
    settings._PATH = None


def test_file_manifest_reports_update_and_persists_last_check(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(
        json.dumps({
            "stable": {
                "latest_version": "99.0.0",
                "release_notes_url": "https://example.invalid/releases/99.0.0",
                "compatibility_notes": "Portable and installer builds are compatible.",
            }
        }),
        encoding="utf-8",
    )

    result = update_checker.check_for_updates(
        manifest_url=f"file://{manifest_path}",
        channel="stable",
        persist=True,
    )

    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["latest_version"] == "99.0.0"
    assert settings.get("updates.last_check.latest_version") == "99.0.0"


def test_default_update_check_is_disabled_and_has_no_placeholder_url(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("default update check should not fetch")

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", unexpected_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["manifest_url"] is None
    assert "example.com" not in json.dumps(result)


def test_github_release_response_reports_update(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "owner/repo")

    def fake_fetch(url, _timeout):
        assert url == "https://api.github.com/repos/owner/repo/releases/latest"
        return {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v99.0.0",
            "body": "Portable build available.",
            "published_at": "2026-04-30T00:00:00Z",
        }

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", fake_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["latest_version"] == "v99.0.0"
    assert result["release_notes_url"].endswith("/v99.0.0")


def test_malformed_github_repo_returns_error_payload(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "bad")

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("malformed github_repo should not fetch")

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", unexpected_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is False
    assert result["enabled"] is True
    assert result["update_available"] is False
    assert "updates.github_repo" in result["error"]


def test_updates_status_route_returns_json_for_malformed_github_repo(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "bad")

    from web.server import app

    response = app.test_client().get("/api/updates/status?force=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["enabled"] is True
    assert "updates.github_repo" in payload["error"]


def test_network_failure_is_non_blocking(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.check_for_updates(
        manifest_url="file://C:/definitely/missing/release.json",
        persist=False,
    )

    assert result["ok"] is False
    assert result["update_available"] is False
    assert result["error"]


def test_dismiss_update_records_version(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.dismiss_update("2.0.0")

    assert result == {"ok": True, "dismissed_version": "2.0.0"}
    assert settings.get("updates.dismissed_version") == "2.0.0"
