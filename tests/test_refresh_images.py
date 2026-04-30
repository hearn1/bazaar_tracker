import json
from types import SimpleNamespace

import app_paths
import db
import refresh_images


class FakeType:
    name = "Texture2D"


class FakeData:
    def __init__(self, name):
        self.m_Name = name
        self.name = name
        self.image = FakeImage()


class FakeImage:
    width = 1024
    height = 1024

    def save(self, path):
        path.write_bytes(b"png")


class FakeObject:
    type = FakeType()

    def __init__(self, path_id, name):
        self.path_id = path_id
        self._name = name

    def read(self):
        return FakeData(self._name)


class FakeUnityPy:
    @staticmethod
    def load(_path):
        obj = FakeObject(7, "CF_M_MAK_TestCard_D")
        return SimpleNamespace(
            container={},
            objects=[obj],
        )


def _point_images_at(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    image_dir = data_dir / "static_cache" / "images"
    monkeypatch.setattr(db, "DB_PATH", data_dir / "bazaar_runs.db")
    monkeypatch.setattr(app_paths, "image_cache_dir", lambda: image_dir)
    db.close_shared_conn()
    return image_dir


def test_refresh_images_extracts_manifest_and_reports_coverage(tmp_path, monkeypatch):
    image_dir = _point_images_at(tmp_path, monkeypatch)
    root = tmp_path / "StandaloneWindows64"
    root.mkdir()
    (root / "card_test.bundle").write_bytes(b"bundle")

    db.init_db()
    conn = db.get_conn()
    try:
        conn.execute(
            """
            INSERT INTO card_cache (template_id, name, card_type, tier, tags, raw_json, cached_at)
            VALUES ('tid-test', 'Test Card', 'Item', 'Bronze', '[]', '{}', 'now')
            """
        )
        conn.commit()
    finally:
        conn.close()

    summary = refresh_images.refresh_images(
        install_root=root,
        out_dir=image_dir,
        UnityPy_module=FakeUnityPy,
    )

    assert summary["bundles_found"] == 1
    assert summary["bundles_loaded"] == 1
    assert summary["manifest_entries"] == 1
    assert summary["coverage"]["coverage_count"] == 1

    manifest = json.loads((image_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "testcard" in manifest["by_card_key"]
    assert (image_dir / manifest["by_card_key"]["testcard"]["image_file"]).exists()


def test_coverage_only_handles_missing_manifest(tmp_path, monkeypatch):
    image_dir = _point_images_at(tmp_path, monkeypatch)
    db.init_db()

    coverage = refresh_images.coverage_report(image_dir)

    assert coverage["manifest_entries"] == 0
    assert coverage["coverage_count"] == 0
