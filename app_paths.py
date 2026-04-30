"""
app_paths.py — Centralized filesystem paths for Bazaar Tracker.

Development defaults stay repo-local so running from a checkout behaves like it
has historically. Packaged builds, or runs with path override env vars, use
per-user data directories so the installed app directory remains read-only.

Override env vars:
  BAZAAR_TRACKER_DATA_DIR      Base dir for DB, logs, and static cache
  BAZAAR_TRACKER_SETTINGS_DIR  Directory containing settings.json
  BAZAAR_TRACKER_DB_PATH       Exact SQLite DB path
  BAZAAR_TRACKER_CACHE_DIR     Static content cache directory
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "BazaarTracker"


def is_packaged() -> bool:
    """True when running from a PyInstaller-style frozen build."""
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Return the read-only app/bundle root for code and packaged assets."""
    if is_packaged():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def repo_dir() -> Path:
    """Return the source checkout / app root directory, or bundled root when frozen."""
    return bundled_root()


def bundled_asset_path(*parts: str) -> Path:
    """Return a path to a read-only asset shipped with the app."""
    return bundled_root().joinpath(*parts)


def _env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).expanduser()


def _windows_known_folder(env_name: str) -> Optional[Path]:
    value = os.environ.get(env_name)
    if value:
        return Path(value) / APP_NAME
    return None


def _platform_data_dir() -> Path:
    """Return the production/user-data base directory for mutable data."""
    if os.name == "nt":
        return _windows_known_folder("LOCALAPPDATA") or (Path.home() / "AppData" / "Local" / APP_NAME)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def _platform_settings_dir() -> Path:
    """Return the production/user-data settings directory."""
    if os.name == "nt":
        return _windows_known_folder("APPDATA") or (Path.home() / "AppData" / "Roaming" / APP_NAME)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def user_data_mode() -> bool:
    """Use user-data paths when packaged or when any path override is supplied."""
    if is_packaged():
        return True
    return any(
        os.environ.get(name)
        for name in (
            "BAZAAR_TRACKER_DATA_DIR",
            "BAZAAR_TRACKER_SETTINGS_DIR",
            "BAZAAR_TRACKER_DB_PATH",
            "BAZAAR_TRACKER_CACHE_DIR",
        )
    )


def data_dir() -> Path:
    """Base directory for mutable data: DB, logs, and cache."""
    override = _env_path("BAZAAR_TRACKER_DATA_DIR")
    if override:
        return override
    if user_data_mode():
        return _platform_data_dir()
    return repo_dir()


def settings_dir() -> Path:
    """Directory containing settings.json."""
    override = _env_path("BAZAAR_TRACKER_SETTINGS_DIR")
    if override:
        return override
    if user_data_mode():
        return _platform_settings_dir()
    return repo_dir()


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def db_path() -> Path:
    override = _env_path("BAZAAR_TRACKER_DB_PATH")
    if override:
        return override
    return data_dir() / "bazaar_runs.db"


def logs_dir() -> Path:
    return data_dir() / "logs"


def static_cache_dir() -> Path:
    override = _env_path("BAZAAR_TRACKER_CACHE_DIR")
    if override:
        return override
    return data_dir() / "static_cache"


def image_cache_dir() -> Path:
    return static_cache_dir() / "images"


def content_manifest_path() -> Path:
    return static_cache_dir() / "content_manifest.json"
