"""
settings.py — Persistent settings management for Bazaar Tracker.

Provides a simple, thread-safe module-level API for getting/setting configuration
values with atomic file I/O. Settings are stored in JSON format with a schema version
for forward compatibility.

Usage:
    import settings
    
    # Load settings from disk (cached after first call)
    settings.load()
    
    # Get a dotted-key value
    width = settings.get("overlay.geometry.width", 320)
    
    # Set a value (in-memory cache only, not persisted until save())
    settings.set("overlay.geometry.width", 400)
    
    # Atomically write the cache to disk
    settings.save()
    
    # Get the resolved settings file path
    path = settings.settings_path()

Location priority:
  1. BAZAAR_TRACKER_SETTINGS_DIR env var override
  2. Repo-local settings.json during development
  3. User settings dir when packaged or path overrides are active
"""

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import app_paths

SCHEMA_VERSION = 3


DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "overlay": {
        "geometry": {
            "x": 100,
            "y": 100,
            "width": 320,
            "height": 520,
        },
        "collapsed": False,
        "always_on_top": True,
    },
    "tracker": {
        "log_path_override": None,
        "skip_mono": False,
        "skip_overlay": False,
        "web_port": 5555,
        "mono_process_name": "TheBazaar.exe",
    },
    "user": {
        "preferred_hero": None,
        "show_dashboard_on_start": False,
    },
    "setup": {
        "completed": False,
        "completed_at": None,
        "last_run_at": None,
        "detected_player_log": None,
        "detected_install_roots": [],
        "detected_cache_roots": [],
        "refresh_content_on_first_run": True,
        "refresh_images_on_first_run": False,
        "steps": {},
    },
    "updates": {
        "enabled": False,
        "channel": "stable",
        "manifest_url": None,
        "github_repo": None,
        "last_check": None,
        "dismissed_version": None,
    },
    "_meta": {
        "last_saved_at": None,
        "last_run_id": None,
    },
}

_CACHE: Optional[dict] = None
_PATH: Optional[Path] = None


def settings_path() -> Path:
    """
    Resolve the settings file path.
    
    Path resolution is delegated to app_paths so settings, DB, logs, and
    content caches all agree on dev vs packaged/user-data behavior.

    Returns:
        Path to the settings.json file.
    """
    global _PATH
    if _PATH is None:
        _PATH = app_paths.settings_path()
    return _PATH

def load() -> dict:
    """
    Load settings from disk (cached after first call).
    
    On first call, reads from disk and caches the result. Subsequent calls
    return a deep copy of the cached data.
    
    If the file doesn't exist, returns DEFAULTS.
    If the file is corrupted, renames it to settings.json.corrupt-<timestamp>
    and returns DEFAULTS.
    
    Returns:
        Deep copy of the settings dict.
    """
    global _CACHE
    if _CACHE is not None:
        return copy.deepcopy(_CACHE)
    
    p = settings_path()
    if not p.exists():
        _CACHE = copy.deepcopy(DEFAULTS)
        return copy.deepcopy(_CACHE)
    
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("root is not a dict")
        migrated = migrate_settings(data)
        _CACHE = _merge_with_defaults(migrated)
        _CACHE["schema_version"] = SCHEMA_VERSION
        return copy.deepcopy(_CACHE)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup = p.with_suffix(f".json.corrupt-{int(time.time())}")
        try:
            p.rename(backup)
            print(f"[Settings] Corrupt settings file moved to {backup}: {e}")
        except OSError:
            pass
        _CACHE = copy.deepcopy(DEFAULTS)
        return copy.deepcopy(_CACHE)


def get(key: str, default: Any = None) -> Any:
    """
    Get a setting value using dotted-key notation.
    
    Examples:
        settings.get("overlay.geometry.width", 320)
        settings.get("tracker.skip_mono", False)
    
    Args:
        key: Dotted path to the setting (e.g., "overlay.geometry.width").
        default: Default value if key not found.
    
    Returns:
        The setting value, or default if not found.
    """
    if _CACHE is None:
        load()
    
    cur = _CACHE
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set(key: str, value: Any) -> None:
    """
    Set a setting value using dotted-key notation.
    
    Creates intermediate dicts as needed. Does NOT write to disk;
    call save() to persist.
    
    Examples:
        settings.set("overlay.geometry.width", 400)
        settings.set("user.preferred_hero", "Mak")
    
    Args:
        key: Dotted path to the setting.
        value: The value to set.
    """
    if _CACHE is None:
        load()
    
    parts = key.split(".")
    cur = _CACHE
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def save() -> bool:
    """
    Atomically write the settings cache to disk.
    
    Uses atomic file operations (temp file + os.replace) to guard
    against corruption from power loss or crashes.
    
    Returns:
        True on success, False on failure.
    """
    if _CACHE is None:
        return True  # nothing loaded → nothing to save
    
    _CACHE.setdefault("_meta", {})["last_saved_at"] = (
        time.strftime("%Y-%m-%dT%H:%M:%S")
    )
    _CACHE["schema_version"] = SCHEMA_VERSION
    
    p = settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(_CACHE, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        os.replace(tmp, p)
        print(f"[Settings] Saved to {p}")
        return True
    except OSError as e:
        print(f"[Settings] Save failed: {e}")
        return False


def migrate_settings(data: dict) -> dict:
    out = copy.deepcopy(data)
    raw_version = out.get("schema_version", 0)
    try:
        current_version = int(raw_version)
    except (TypeError, ValueError):
        current_version = 0

    if current_version > SCHEMA_VERSION:
        print(
            f"[Settings] WARNING: settings schema {current_version} is newer "
            f"than app schema {SCHEMA_VERSION}."
        )
        return out

    out["schema_version"] = SCHEMA_VERSION
    return out


def schema_version() -> int:
    return SCHEMA_VERSION


def _merge_with_defaults(loaded: dict) -> dict:
    """
    Recursively merge loaded settings with DEFAULTS.
    
    Ensures all schema keys exist with fallback values, while
    preserving any user-set values from the loaded file.
    
    Args:
        loaded: Settings loaded from disk.
    
    Returns:
        Merged dict with all defaults present.
    """
    out = copy.deepcopy(DEFAULTS)
    
    def _merge(target, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                _merge(target[k], v)
            else:
                target[k] = v
    
    _merge(out, loaded)
    return out
