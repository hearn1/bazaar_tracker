import json

import settings


def _point_settings_at(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "_PATH", path)
    monkeypatch.setattr(settings, "_CACHE", None)
    return path


def test_load_migrates_and_merges_defaults(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "overlay": {"geometry": {"width": 444}},
                "tracker": {"web_port": 7777},
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert loaded["overlay"]["geometry"]["width"] == 444
    assert loaded["overlay"]["geometry"]["height"] == settings.DEFAULTS["overlay"]["geometry"]["height"]
    assert loaded["tracker"]["web_port"] == 7777
    assert "user" in loaded


def test_save_writes_current_schema_version(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    settings.load()
    settings.set("tracker.web_port", 6060)

    assert settings.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == settings.SCHEMA_VERSION
    assert saved["tracker"]["web_port"] == 6060


def test_corrupt_settings_are_backed_up(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text("{not json", encoding="utf-8")

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert not path.exists()
    assert list(tmp_path.glob("settings.json.corrupt-*"))
