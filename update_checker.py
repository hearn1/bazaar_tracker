"""Non-blocking release update checks for Bazaar Tracker."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import settings
from version import APP_VERSION

DEFAULT_RELEASE_MANIFEST_URL = None
CHANNELS = {"stable", "beta", "dev"}


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _parse_version(value: str) -> tuple:
    raw = value.strip().lower()
    if raw.startswith("v"):
        raw = raw[1:]
    main, _, suffix = raw.partition("-")
    parts: list[Any] = []
    for piece in main.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(piece)
    if suffix:
        parts.append(suffix)
    return tuple(parts)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return latest.strip() != current.strip()


def _load_manifest_from_url(url: str, timeout: float) -> dict:
    if url.startswith("file://"):
        return json.loads(Path(url[7:]).read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": f"BazaarTracker/{APP_VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _github_latest_release_url(repo: str) -> str:
    repo = str(repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        raise ValueError("updates.github_repo must be in owner/repo form")
    return f"https://api.github.com/repos/{repo}/releases/latest"


def normalize_manifest(data: dict, channel: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("release manifest root must be an object")

    selected = data.get(channel)
    if isinstance(selected, dict):
        release = selected
    else:
        releases = data.get("channels")
        if isinstance(releases, dict) and isinstance(releases.get(channel), dict):
            release = releases[channel]
        else:
            release = data

    latest_version = str(
        release.get("latest_version")
        or release.get("version")
        or release.get("tag_name")
        or ""
    ).strip()
    if not latest_version:
        raise ValueError("release manifest is missing latest_version")

    return {
        "channel": str(release.get("channel") or channel),
        "latest_version": latest_version,
        "release_notes_url": release.get("release_notes_url") or release.get("notes_url") or release.get("html_url"),
        "download_url": release.get("download_url"),
        "compatibility_notes": release.get("compatibility_notes") or release.get("compatibility") or release.get("body") or "",
        "minimum_supported_version": release.get("minimum_supported_version"),
        "published_at": release.get("published_at"),
    }


def check_for_updates(
    manifest_url: Optional[str] = None,
    channel: Optional[str] = None,
    timeout: float = 2.5,
    persist: bool = True,
) -> dict:
    settings.load()
    update_settings = settings.get("updates", {})
    explicit_source = manifest_url or update_settings.get("manifest_url") or update_settings.get("github_repo")
    enabled = bool(manifest_url) or (bool(update_settings.get("enabled", False)) and bool(explicit_source))
    selected_channel = channel or update_settings.get("channel", "stable")
    if selected_channel not in CHANNELS:
        selected_channel = "stable"
    if manifest_url or update_settings.get("manifest_url"):
        url = manifest_url or update_settings.get("manifest_url")
    elif update_settings.get("github_repo"):
        url = None
    else:
        url = DEFAULT_RELEASE_MANIFEST_URL

    result = {
        "ok": False,
        "enabled": enabled,
        "checked_at": _utc_now(),
        "channel": selected_channel,
        "current_version": APP_VERSION,
        "manifest_url": url,
        "update_available": False,
        "dismissed": False,
        "latest_version": None,
        "release_notes_url": None,
        "download_url": None,
        "compatibility_notes": "",
        "error": None,
    }
    if not enabled:
        result["ok"] = True
        result["manifest_url"] = None
        return result

    try:
        if url is None and update_settings.get("github_repo"):
            url = _github_latest_release_url(update_settings.get("github_repo"))
            result["manifest_url"] = url
        manifest = normalize_manifest(_load_manifest_from_url(url, timeout), selected_channel)
        result.update(manifest)
        result["ok"] = True
        result["update_available"] = is_newer_version(manifest["latest_version"], APP_VERSION)
        dismissed_version = update_settings.get("dismissed_version")
        result["dismissed"] = bool(dismissed_version and dismissed_version == manifest["latest_version"])
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        result["error"] = str(exc)

    if persist:
        settings.set("updates.last_check", result)
        settings.save()
    return result


def dismiss_update(version: str) -> dict:
    settings.load()
    settings.set("updates.dismissed_version", version)
    settings.save()
    return {"ok": True, "dismissed_version": version}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check Bazaar Tracker release manifest")
    parser.add_argument("--manifest-url", default=None)
    parser.add_argument("--channel", choices=sorted(CHANNELS), default=None)
    parser.add_argument("--timeout", type=float, default=2.5)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)
    result = check_for_updates(
        manifest_url=args.manifest_url,
        channel=args.channel,
        timeout=args.timeout,
        persist=not args.no_save,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
