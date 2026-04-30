"""Doctor checks and diagnostic export for Bazaar Tracker."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app_paths
import content_manifest
import db
import settings
import refresh_images
from version import APP_VERSION


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(name: str, status: str, message: str, **details) -> CheckResult:
    return CheckResult(name, status, message, details or None)


def _path_writable(path: Path, *, is_dir: bool = True) -> tuple[bool, str]:
    try:
        target_dir = path if is_dir else path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".doctor-", dir=target_dir, delete=True):
            pass
        return True, str(target_dir)
    except Exception as exc:
        return False, str(exc)


def _connect_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _can_bind_port(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _load_image_manifest() -> dict:
    path = app_paths.image_cache_dir() / "manifest.json"
    if not path.exists():
        return {"exists": False, "path": str(path), "by_card_key": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest root is not an object")
        data["exists"] = True
        data["path"] = str(path)
        data.setdefault("by_card_key", {})
        return data
    except Exception as exc:
        return {"exists": True, "path": str(path), "error": str(exc), "by_card_key": {}}


def dependency_versions() -> dict[str, str]:
    packages = ["flask", "waitress", "requests", "watchdog", "pywebview", "frida", "pytest"]
    module_names = {"pywebview": "webview"}
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            module_name = module_names.get(package, package)
            try:
                importlib.import_module(module_name)
                versions[package] = "bundled"
            except ImportError:
                versions[package] = "not installed"
    return versions


def _detect_bazaar_process(process_name: str = "TheBazaar.exe") -> dict:
    if os.name != "nt":
        return {"checked": False, "reason": "process check is Windows-only"}
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = completed.stdout.strip()
        running = process_name.lower() in output.lower()
        return {
            "checked": True,
            "running": running,
            "process_name": process_name,
        }
    except Exception as exc:
        return {"checked": False, "error": str(exc), "process_name": process_name}


def find_player_log_path() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""), "..", "LocalLow", "Tempo Storm", "The Bazaar", "Player.log"),
        Path(os.environ.get("USERPROFILE", "C:/Users/User"), "AppData", "LocalLow", "Tempo Storm", "The Bazaar", "Player.log"),
        Path("Player.log"),
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[1].resolve()


def collect_db_summary() -> dict:
    db_path = app_paths.db_path()
    summary = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "schema_version": None,
        "expected_schema_version": db.SCHEMA_VERSION,
        "tables": {},
    }
    if not db_path.exists():
        return summary

    conn = db.get_conn()
    try:
        summary["schema_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for row in table_rows:
            table = row["name"]
            columns = [
                dict(col)
                for col in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            summary["tables"][table] = {
                "row_count": _count_rows(conn, table),
                "columns": columns,
            }
    finally:
        conn.close()
    return summary


def collect_unresolved_template_report(limit: int = 100) -> dict:
    db_path = app_paths.db_path()
    report = {"path": str(db_path), "items": []}
    if not db_path.exists():
        return report

    conn = db.get_conn()
    try:
        if _table_exists(conn, "decisions"):
            rows = conn.execute(
                """
                SELECT chosen_template AS template_id, COUNT(*) AS count, 'decisions.chosen_template' AS source
                FROM decisions
                LEFT JOIN card_cache ON decisions.chosen_template = card_cache.template_id
                WHERE chosen_template IS NOT NULL
                  AND chosen_template != ''
                  AND card_cache.template_id IS NULL
                GROUP BY chosen_template
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            report["items"].extend(dict(row) for row in rows)

        remaining = max(0, limit - len(report["items"]))
        if remaining and _table_exists(conn, "api_cards"):
            rows = conn.execute(
                """
                SELECT api_cards.template_id AS template_id, COUNT(*) AS count, 'api_cards.template_id' AS source
                FROM api_cards
                LEFT JOIN card_cache ON api_cards.template_id = card_cache.template_id
                WHERE api_cards.template_id IS NOT NULL
                  AND api_cards.template_id != ''
                  AND card_cache.template_id IS NULL
                GROUP BY api_cards.template_id
                ORDER BY count DESC
                LIMIT ?
                """,
                (remaining,),
            ).fetchall()
            report["items"].extend(dict(row) for row in rows)
    finally:
        conn.close()
    return report


def collect_image_coverage() -> dict:
    manifest = _load_image_manifest()
    db_path = app_paths.db_path()
    card_count = 0
    if db_path.exists():
        conn = db.get_conn()
        try:
            card_count = _count_rows(conn, "card_cache")
        finally:
            conn.close()
    manifest_count = len(manifest.get("by_card_key") or {})
    return {
        "manifest_path": manifest.get("path"),
        "manifest_exists": bool(manifest.get("exists")),
        "manifest_error": manifest.get("error"),
        "manifest_entries": manifest_count,
        "card_cache_rows": card_count,
        "coverage_percent": round((manifest_count / card_count) * 100, 1) if card_count else 0,
    }


def collect_doctor_report() -> dict:
    settings.load()
    checks: list[CheckResult] = []

    checks.append(_result(
        "app",
        "ok",
        f"Bazaar Tracker {APP_VERSION} on Python {platform.python_version()}",
        app_version=APP_VERSION,
        python=sys.version,
        platform=platform.platform(),
        packaged=app_paths.is_packaged(),
        user_data_mode=app_paths.user_data_mode(),
    ))

    for label, path, is_dir in [
        ("data path", app_paths.data_dir(), True),
        ("settings path", app_paths.settings_path(), False),
        ("database path", app_paths.db_path(), False),
        ("cache path", app_paths.static_cache_dir(), True),
        ("logs path", app_paths.logs_dir(), True),
        ("image cache path", app_paths.image_cache_dir(), True),
    ]:
        ok, detail = _path_writable(path, is_dir=is_dir)
        checks.append(_result(
            label,
            "ok" if ok else "fail",
            f"Writable: {path}" if ok else f"Not writable: {path}",
            detail=detail,
            path=str(path),
        ))

    migration_error = None
    try:
        db.ensure_schema()
    except Exception as exc:
        migration_error = str(exc)

    db_summary = collect_db_summary()
    if not db_summary["exists"]:
        checks.append(_result("database", "warn", f"Database does not exist yet: {db_summary['path']}"))
    elif migration_error:
        checks.append(_result(
            "database",
            "fail",
            f"DB migration failed: {migration_error}",
            path=db_summary["path"],
        ))
    elif db_summary["schema_version"] != db_summary["expected_schema_version"]:
        checks.append(_result(
            "database",
            "fail",
            f"DB schema v{db_summary['schema_version']} != expected v{db_summary['expected_schema_version']}",
            path=db_summary["path"],
        ))
    else:
        checks.append(_result(
            "database",
            "ok",
            f"DB schema v{db_summary['schema_version']} is current",
            path=db_summary["path"],
        ))

    card_rows = db_summary.get("tables", {}).get("card_cache", {}).get("row_count", 0)
    if card_rows >= 100:
        checks.append(_result("card cache", "ok", f"Card cache has {card_rows:,} rows"))
    elif card_rows:
        checks.append(_result("card cache", "warn", f"Card cache only has {card_rows:,} rows"))
    else:
        checks.append(_result("card cache", "warn", "Card cache is empty or missing"))

    manifest = content_manifest.load_manifest()
    manifest_summary = content_manifest.summarize_manifest(manifest)
    checks.append(_result(
        "content manifest",
        "warn" if manifest.get("load_error") or not manifest.get("endpoints") else "ok",
        manifest_summary,
    ))

    image_coverage = collect_image_coverage()
    try:
        detailed_image_coverage = refresh_images.coverage_report()
        image_coverage.update({
            "coverage_count": detailed_image_coverage.get("coverage_count"),
            "missing_count": detailed_image_coverage.get("missing_count"),
            "missing_sample": detailed_image_coverage.get("missing_sample", []),
        })
    except Exception as exc:
        image_coverage["coverage_detail_error"] = str(exc)
    if image_coverage["manifest_error"]:
        image_status = "fail"
        image_message = f"Image manifest is unreadable: {image_coverage['manifest_error']}"
    elif image_coverage["manifest_exists"]:
        image_status = "ok" if image_coverage["manifest_entries"] else "warn"
        image_message = (
            f"Image manifest has {image_coverage['manifest_entries']:,} entries "
            f"({image_coverage['coverage_percent']}% of card_cache rows)"
        )
    else:
        image_status = "warn"
        image_message = f"Image manifest missing at {image_coverage['manifest_path']}"
    checks.append(_result("image manifest", image_status, image_message, **image_coverage))

    log_path = find_player_log_path()
    checks.append(_result(
        "Player.log",
        "ok" if log_path.exists() else "warn",
        f"Found Player.log at {log_path}" if log_path.exists() else f"Player.log not found at expected path: {log_path}",
        path=str(log_path),
    ))

    deps = dependency_versions()
    missing_runtime = [name for name in ("flask", "waitress", "requests") if deps.get(name) == "not installed"]
    checks.append(_result(
        "dependencies",
        "fail" if missing_runtime else "ok",
        "Missing runtime packages: " + ", ".join(missing_runtime) if missing_runtime else "Runtime dependencies are importable",
        versions=deps,
    ))

    process_info = _detect_bazaar_process(settings.get("tracker.mono_process_name", "TheBazaar.exe"))
    if process_info.get("checked"):
        checks.append(_result(
            "Bazaar process",
            "ok" if process_info.get("running") else "warn",
            f"{process_info['process_name']} is running" if process_info.get("running") else f"{process_info['process_name']} is not running",
            **process_info,
        ))
    else:
        checks.append(_result("Bazaar process", "warn", "Could not check Bazaar process", **process_info))

    frida_version = deps.get("frida")
    checks.append(_result(
        "Frida",
        "ok" if frida_version and frida_version != "not installed" else "warn",
        f"frida {frida_version}" if frida_version != "not installed" else "frida is not installed; Mono capture will not work",
        version=frida_version,
    ))

    port = int(settings.get("tracker.web_port", 5555) or 5555)
    if _connect_port("127.0.0.1", port):
        checks.append(_result("web port", "warn", f"Port {port} is already in use", port=port))
    elif _can_bind_port("127.0.0.1", port):
        checks.append(_result("web port", "ok", f"Port {port} is available", port=port))
    else:
        checks.append(_result("web port", "fail", f"Port {port} cannot be bound", port=port))

    return {
        "generated_at": utc_now_iso(),
        "checks": [asdict(check) for check in checks],
        "db_summary": db_summary,
        "content_manifest": manifest,
        "image_coverage": image_coverage,
        "dependency_versions": deps,
    }


def _print_report(report: dict) -> int:
    rank = {"ok": 0, "warn": 1, "fail": 2}
    worst = 0
    for check in report["checks"]:
        status = check["status"].upper()
        worst = max(worst, rank.get(check["status"], 1))
        print(f"[{status:4}] {check['name']}: {check['message']}")
    return 1 if worst == 2 else 0


def _scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if (":\\" in value or "/" in value or "appdata" in lowered or "users\\" in lowered) and len(value) > 3:
            return "<path scrubbed>"
    return value


def _write_json(zipf: zipfile.ZipFile, name: str, data: Any) -> None:
    zipf.writestr(name, json.dumps(data, indent=2, sort_keys=True, default=str))


def export_diagnostics(output: Path | None = None, *, include_db: bool = False) -> Path:
    report = collect_doctor_report()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = output or (app_paths.logs_dir() / f"bazaar_tracker_diagnostics_{stamp}.zip")
    output.parent.mkdir(parents=True, exist_ok=True)

    settings_data = settings.load()
    unresolved_report = collect_unresolved_template_report()
    db_summary = report["db_summary"]

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        _write_json(zipf, "doctor_report.json", report)
        _write_json(zipf, "settings_scrubbed.json", _scrub_value(settings_data))
        _write_json(zipf, "db_schema_summary.json", db_summary)
        _write_json(zipf, "content_manifest.json", report["content_manifest"])
        _write_json(zipf, "image_coverage.json", report["image_coverage"])
        _write_json(zipf, "unresolved_template_ids.json", unresolved_report)
        _write_json(zipf, "dependency_versions.json", report["dependency_versions"])

        lines = [
            f"[{check['status'].upper():4}] {check['name']}: {check['message']}"
            for check in report["checks"]
        ]
        zipf.writestr("doctor.txt", "\n".join(lines) + "\n")

        logs_dir = app_paths.logs_dir()
        if logs_dir.exists():
            for log_path in sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
                zipf.write(log_path, f"logs/{log_path.name}")

        if include_db and app_paths.db_path().exists():
            zipf.write(app_paths.db_path(), f"optional/{app_paths.db_path().name}")

    return output


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"doctor", "export-diagnostics"}:
        command = argv.pop(0)
    else:
        command = "doctor"

    parser = argparse.ArgumentParser(description="Bazaar Tracker diagnostics")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output", type=Path, default=None, help="Diagnostics zip output path")
    parser.add_argument("--include-db", action="store_true", help="Include the full run database in the diagnostics zip")
    args = parser.parse_args(argv)

    if command == "export-diagnostics":
        path = export_diagnostics(args.output, include_db=args.include_db)
        print(f"[Diagnostics] Wrote {path}")
        return 0

    report = collect_doctor_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0
    return _print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
