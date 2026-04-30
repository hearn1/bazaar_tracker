"""First-run setup checklist for Bazaar Tracker."""

from __future__ import annotations

import importlib.metadata
import importlib
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import app_paths
import card_cache
import db
import refresh_images
import settings


@dataclass
class SetupStep:
    id: str
    label: str
    status: str
    message: str
    details: dict | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step(step_id: str, label: str, status: str, message: str, **details) -> SetupStep:
    return SetupStep(step_id, label, status, message, details or None)


def find_player_log_path() -> Path:
    candidates = [
        Path.home() / "AppData" / "LocalLow" / "Tempo Storm" / "The Bazaar" / "Player.log",
        Path("Player.log"),
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[0].resolve()


def _port_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _dependency_version(package: str) -> Optional[str]:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        module_name = {"pywebview": "webview"}.get(package, package)
        try:
            importlib.import_module(module_name)
            return "bundled"
        except ImportError:
            return None


def _steps_to_settings(steps: list[SetupStep]) -> dict:
    return {
        step.id: {
            "label": step.label,
            "status": step.status,
            "message": step.message,
            "details": step.details or {},
        }
        for step in steps
    }


def setup_completed() -> bool:
    return bool(settings.get("setup.completed", False))


def setup_status() -> dict:
    settings.load()
    steps = settings.get("setup.steps", {}) or {}
    return {
        "completed": bool(settings.get("setup.completed", False)),
        "completed_at": settings.get("setup.completed_at"),
        "last_run_at": settings.get("setup.last_run_at"),
        "detected_player_log": settings.get("setup.detected_player_log"),
        "detected_install_roots": settings.get("setup.detected_install_roots", []),
        "detected_cache_roots": settings.get("setup.detected_cache_roots", []),
        "steps": steps,
    }


def run_setup(
    *,
    force: bool = False,
    refresh_content: str = "auto",
    refresh_images_enabled: bool = False,
    content_refresh_fn: Optional[Callable[..., dict]] = None,
    image_refresh_fn: Optional[Callable[..., dict]] = None,
) -> dict:
    """Run a noninteractive first-run checklist and persist the result.

    refresh_content:
        "auto" refreshes only when card_cache is not populated.
        "always" refreshes every run.
        "never" skips network content refresh.
    """
    settings.load()
    if setup_completed() and not force:
        return setup_status()

    content_refresh_fn = content_refresh_fn or card_cache.refresh_cache
    image_refresh_fn = image_refresh_fn or refresh_images.refresh_images
    started_at = utc_now_iso()
    steps: list[SetupStep] = []

    try:
        db.init_db()
        steps.append(_step("database", "Initialize database", "ok", "Database initialized", path=str(app_paths.db_path())))
    except Exception as exc:
        steps.append(_step("database", "Initialize database", "fail", str(exc), path=str(app_paths.db_path())))

    player_log = find_player_log_path()
    settings.set("setup.detected_player_log", str(player_log))
    if player_log.exists():
        steps.append(_step("player_log", "Locate Player.log", "ok", f"Found {player_log}", path=str(player_log)))
    else:
        steps.append(_step("player_log", "Locate Player.log", "warn", f"Not found at expected path: {player_log}", path=str(player_log)))

    cache_populated = card_cache.is_cache_populated()
    should_refresh_content = refresh_content == "always" or (refresh_content == "auto" and not cache_populated)
    if should_refresh_content:
        try:
            summary = content_refresh_fn(versioned=True)
            card_count = int(summary.get("card_count") or len(summary.get("cards") or []))
            steps.append(_step("content_cache", "Refresh static content", "ok", f"Cached {card_count:,} content entries", summary={k: v for k, v in summary.items() if k != "cards"}))
        except Exception as exc:
            steps.append(_step("content_cache", "Refresh static content", "warn", f"{exc}; app can run with the existing local cache"))
    elif cache_populated:
        steps.append(_step("content_cache", "Refresh static content", "ok", "Static content cache already populated"))
    else:
        steps.append(_step("content_cache", "Refresh static content", "warn", "Static content refresh skipped and cache is empty"))

    install_roots = [str(path) for path in refresh_images.detect_install_roots()]
    cache_roots = [str(path) for path in refresh_images.detect_cache_roots()]
    settings.set("setup.detected_install_roots", install_roots)
    settings.set("setup.detected_cache_roots", cache_roots)
    if install_roots or cache_roots:
        steps.append(_step("bundle_roots", "Detect Bazaar install/cache", "ok", "Detected local bundle roots", install_roots=install_roots, cache_roots=cache_roots))
    else:
        steps.append(_step("bundle_roots", "Detect Bazaar install/cache", "warn", "No local bundle roots detected; refresh-images can use explicit paths"))

    if refresh_images_enabled:
        try:
            summary = image_refresh_fn()
            steps.append(_step("image_cache", "Refresh card images", "ok", f"Extracted {summary.get('manifest_entries', 0):,} image entries", summary=summary))
        except Exception as exc:
            steps.append(_step("image_cache", "Refresh card images", "warn", str(exc)))
    else:
        try:
            coverage = refresh_images.coverage_report()
            manifest_entries = coverage.get("manifest_entries", 0)
            status = "ok" if manifest_entries else "warn"
            message = f"Image refresh skipped; existing manifest has {manifest_entries:,} entries"
            steps.append(_step("image_cache", "Refresh card images", status, message, coverage=coverage))
        except Exception as exc:
            steps.append(_step("image_cache", "Refresh card images", "warn", f"Image refresh skipped; coverage unavailable: {exc}"))

    port = int(settings.get("tracker.web_port", 5555) or 5555)
    if _port_available(port):
        steps.append(_step("web_port", "Verify web port", "ok", f"Port {port} is available", port=port))
    else:
        steps.append(_step("web_port", "Verify web port", "warn", f"Port {port} is already in use", port=port))

    frida_version = _dependency_version("frida")
    if frida_version:
        steps.append(_step("frida", "Check Frida prerequisite", "ok", f"frida {frida_version}", version=frida_version))
    else:
        steps.append(_step("frida", "Check Frida prerequisite", "warn", "frida is not installed; Mono capture will not work"))

    failed = [step for step in steps if step.status == "fail"]
    completed = not failed
    finished_at = utc_now_iso()
    settings.set("setup.completed", completed)
    settings.set("setup.completed_at", finished_at if completed else None)
    settings.set("setup.last_run_at", finished_at)
    settings.set("setup.steps", _steps_to_settings(steps))
    settings.save()

    return {
        "completed": completed,
        "started_at": started_at,
        "finished_at": finished_at,
        "steps": [asdict(step) for step in steps],
    }


def print_setup_report(report: dict) -> None:
    print("[Setup] First-run setup report:")
    print(f"  Completed: {bool(report.get('completed'))}")
    steps = report.get("steps", [])
    if isinstance(steps, dict):
        steps = [
            {"id": step_id, **(step if isinstance(step, dict) else {})}
            for step_id, step in steps.items()
        ]
    for step in steps:
        print(f"  [{step['status'].upper():4}] {step['label']}: {step['message']}")
