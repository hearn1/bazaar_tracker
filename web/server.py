"""
web/server.py — Flask API + static file server for the Bazaar Tracker dashboard.

This file owns only route definitions and the server lifecycle. All business
logic has been extracted to focused modules:

  web/build_helpers.py   — build catalog loading, archetype scoring, phase notes,
                           run-tier classification, insight extraction
  web/overlay_state.py   — /api/overlay/state payload assembly
  web/review_builder.py  — overlay review row construction

Usage:
    cd bazaar_tracker
    python -m web.server                  # standalone on port 5555
    python -m web.server --port 8080      # custom port

Or import and start from tracker.py:
    from web.server import start_web_server
    start_web_server(port=5555, db_path="bazaar_runs.db")
"""

import json
import re
import sqlite3
import argparse
import threading
import sys
from typing import Optional, Callable
from pathlib import Path

import app_paths

ROOT_DIR = app_paths.bundled_root()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import card_cache
import first_run
import update_checker

from flask import Flask, jsonify, request, send_from_directory
from name_resolver import is_unresolved, make_resolver

from web.build_helpers import (
    load_builds,
    condition_items_for_archetype,
    infer_archetype_from_decisions,
    build_run_summary,
)
from web.overlay_state import build_overlay_state, _get_pvp_record
from web.review_builder import format_decision_row
from web.card_images import IMAGE_DIR as CARD_IMAGE_DIR, lookup_image_url

DEFAULT_PORT = 5555
DB_PATH: Optional[Path] = None
_shutdown_callback: Optional[Callable] = None

app = Flask(
    __name__,
    static_folder=str(app_paths.bundled_asset_path("web", "static")),
    static_url_path="/static",
)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db_path() -> Path:
    if DB_PATH:
        return DB_PATH
    return app_paths.db_path()

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _safe_json(raw) -> list | dict:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, (list, dict)) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _resolve(conn, template_id: str) -> str:
    if not template_id:
        return "Unknown"
    row = conn.execute(
        "SELECT name FROM card_cache WHERE template_id=?", (template_id,)
    ).fetchone()
    if row and row["name"] and row["name"] != "Unknown":
        return row["name"]
    return template_id


def _get_tier(conn, template_id: str) -> Optional[str]:
    if not template_id:
        return None
    row = conn.execute(
        "SELECT tier FROM card_cache WHERE template_id=?", (template_id,)
    ).fetchone()
    return row["tier"] if row else None


def _resolve_image(conn, template_id: str) -> Optional[str]:
    """Return /cards/<filename> URL for a template_id, or None.

    Chain: template_id -> card_cache.name -> normalize -> manifest -> URL.
    """
    if not template_id:
        return None
    name = _resolve(conn, template_id)
    if not name:
        return None
    return lookup_image_url(name)


def _resolve_chosen_template(d: dict) -> str:
    chosen_template = d.get("chosen_template") or ""
    if chosen_template:
        return chosen_template
    offered_templates = _safe_json(d.get("offered_templates"))
    if isinstance(offered_templates, dict):
        return offered_templates.get(d.get("chosen_id"), "") or ""
    return ""


def _resolve_instance_ids_via_api_cards(conn, instance_ids: list[str]) -> dict[str, str]:
    if not instance_ids:
        return {}
    resolver = make_resolver()
    mapping = resolver.bulk_resolve(instance_ids)
    return {iid: name for iid, name in mapping.items() if not is_unresolved(name)}


_ARCHETYPE_PLACEHOLDER_LOWERS = {"", "none", "null", "unknown", "no archetype fit", "no fit"}


def _clean_archetype_label(archetype: Optional[str]) -> Optional[str]:
    if isinstance(archetype, str) and archetype.strip().lower() not in _ARCHETYPE_PLACEHOLDER_LOWERS:
        return archetype.strip()
    return None


# ── Routes — static ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/overlay")
def overlay_page():
    return send_from_directory(app.static_folder, "overlay.html")


@app.route("/cards/<path:filename>")
def card_image(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return ("", 404)
    path = CARD_IMAGE_DIR / filename
    if not path.is_file():
        return ("", 404)
    response = send_from_directory(CARD_IMAGE_DIR, filename)
    response.cache_control.max_age = 86400 * 30
    response.cache_control.public = True
    return response


# ── Routes — builds ───────────────────────────────────────────────────────────

@app.route("/api/builds/archetypes", defaults={"hero": None})
@app.route("/api/builds/archetypes/<hero>")
def api_builds_archetypes(hero: Optional[str]):
    if hero is None:
        conn = _conn()
        try:
            latest_run = conn.execute(
                "SELECT hero FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            hero = latest_run["hero"] if latest_run else None
        finally:
            conn.close()

    build_data, relevant_items = load_builds(hero)
    archetypes = []
    for phase, phase_data in build_data.get("game_phases", {}).items():
        for arch in phase_data.get("archetypes", []):
            archetypes.append({
                "name": arch["name"],
                "phase": phase,
                "core_items": arch.get("core_items", []),
                "carry_items": arch.get("carry_items", []),
                "support_items": arch.get("support_items", []),
                "condition": arch.get("condition"),
                "condition_items": condition_items_for_archetype(
                    arch, relevant_items=relevant_items,
                ),
                "notes": arch.get("notes"),
                "pivot_from": arch.get("pivot_from", []),
            })
    return jsonify({
        "hero": build_data.get("hero"),
        "archetypes": archetypes,
        "item_tier_list": build_data.get("item_tier_list", {}),
        "hero_notes": build_data.get("notes", ""),
        "pivot_signals": build_data.get("pivot_signals", {}).get("signals", []),
    })


@app.route("/api/builds/items/<hero>")
def api_builds_items(hero: str):
    """Return {item_name: '/cards/<filename>'} for every build-relevant item.

    Items missing from the manifest are omitted (not nulled). The overlay's
    lookup is dict.get(name), so absence and null fall through identically.
    """
    try:
        _build_data, relevant_items = load_builds(hero)
    except Exception as exc:
        print(f"[CardImages] load_builds failed for {hero!r}: {exc}")
        return jsonify({})

    result: dict[str, str] = {}
    for item_name in relevant_items or []:
        url = lookup_image_url(item_name)
        if url:
            result[item_name] = url
    return jsonify(result)


# ── Routes — overlay ──────────────────────────────────────────────────────────

@app.route("/api/overlay/state")
def api_overlay_state():
    conn = _conn()
    try:
        state = build_overlay_state(
            conn,
            resolve_fn=_resolve,
            safe_json_fn=_safe_json,
            lookup_image_by_name_fn=lookup_image_url,
        )
        if "error" in state:
            return jsonify(state), 404
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── Routes — runs ─────────────────────────────────────────────────────────────

@app.route("/api/runs")
def api_runs():
    conn = _conn()
    try:
        runs = conn.execute("""
            SELECT r.*, COUNT(d.id) as decision_count
            FROM runs r
            LEFT JOIN decisions d ON d.run_id = r.id
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT 30
        """).fetchall()

        result = []
        for r in runs:
            r = dict(r)
            build_data, _relevant_items = load_builds(r.get("hero"))
            pvp_w, pvp_l = _get_pvp_record(conn, r["id"], r)
            pve = conn.execute("""
                SELECT
                    SUM(CASE WHEN outcome='opponent_died' AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) as w,
                    SUM(CASE WHEN outcome='player_died' AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) as l
                FROM combat_results WHERE run_id=?
            """, (r["id"],)).fetchone()

            archetype = None
            arch_row = conn.execute("""
                SELECT score_notes FROM decisions
                WHERE run_id=? AND score_notes LIKE '%COMMITTED%'
                ORDER BY decision_seq LIMIT 1
            """, (r["id"],)).fetchone()
            if arch_row:
                m = re.search(r'COMMITTED to ([\w\s\-]+?)(?:\s*\(|\.)', arch_row["score_notes"])
                if m:
                    archetype = _clean_archetype_label(m.group(1))
            if not archetype:
                inferred_name, _ = infer_archetype_from_decisions(
                    conn, r["id"], build_data=build_data, resolve_fn=_resolve,
                )
                archetype = _clean_archetype_label(inferred_name)

            result.append({
                "id": r["id"], "hero": r["hero"], "outcome": r["outcome"],
                "started_at": r["started_at"], "ended_at": r.get("ended_at"),
                "pvp_wins": pvp_w, "pvp_losses": pvp_l,
                "pve_wins": pve["w"] if pve else 0, "pve_losses": pve["l"] if pve else 0,
                "decision_count": r["decision_count"], "archetype": archetype,
            })
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/decisions")
def api_decisions(run_id: int):
    conn = _conn()
    try:
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY decision_seq", (run_id,)
        ).fetchall()
        result = [
            format_decision_row(
                dict(d),
                resolve_fn=lambda tid: _resolve(conn, tid),
                get_tier_fn=lambda tid: _get_tier(conn, tid),
                safe_json_fn=_safe_json,
                resolve_instance_ids_fn=lambda ids: _resolve_instance_ids_via_api_cards(conn, ids),
                is_unresolved_fn=is_unresolved,
                resolve_image_fn=lambda tid: _resolve_image(conn, tid),
            )
            for d in decisions
        ]
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/combats")
def api_combats(run_id: int):
    conn = _conn()
    try:
        combats = conn.execute("""
            SELECT outcome, combat_type, duration_secs, timestamp
            FROM combat_results WHERE run_id=? ORDER BY id
        """, (run_id,)).fetchall()
        return jsonify(_rows_to_dicts(combats))
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/summary")
def api_summary(run_id: int):
    conn = _conn()
    try:
        result = build_run_summary(conn, run_id, resolve_fn=_resolve)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/status")
def api_status():
    db_path = _get_db_path()
    return jsonify({
        "db_exists": db_path.exists(),
        "db_path": str(db_path),
        "db_size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0,
    })


@app.route("/api/content/status")
def api_content_status():
    return jsonify(card_cache.content_status())


@app.route("/api/setup/status")
def api_setup_status():
    return jsonify(first_run.setup_status())


@app.route("/api/updates/status")
def api_updates_status():
    force = request.args.get("force") in {"1", "true", "yes"}
    try:
        import settings

        settings.load()
        last_check = settings.get("updates.last_check")
    except Exception:
        last_check = None
    if force or not last_check:
        try:
            return jsonify(update_checker.check_for_updates(persist=True))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)})
    return jsonify(last_check)


@app.route("/api/updates/dismiss", methods=["POST"])
def api_updates_dismiss():
    payload = request.get_json(silent=True) or {}
    version = payload.get("version")
    if not version:
        current = update_checker.check_for_updates(persist=False)
        version = current.get("latest_version")
    if not version:
        return jsonify({"ok": False, "error": "no version to dismiss"}), 400
    return jsonify(update_checker.dismiss_update(str(version)))


# ── Routes — control ──────────────────────────────────────────────────────────

@app.route("/api/control/shutdown", methods=["POST"])
def api_control_shutdown():
    """
    Graceful shutdown trigger endpoint.
    
    Called by the overlay's Quit button or can be called from curl/external clients.
    Dispatches the shutdown event on a daemon thread so the HTTP response can be sent
    before the server begins to shut down.
    
    Returns:
        JSON response with status.
    """
    if _shutdown_callback:
        threading.Thread(target=_shutdown_callback, daemon=True).start()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "no shutdown handler registered"}), 500


# ── Server lifecycle ──────────────────────────────────────────────────────────

def set_shutdown_callback(cb: Callable) -> None:
    """
    Register a callback to be invoked when /api/control/shutdown is posted.
    
    Args:
        cb: A callable that initiates shutdown (e.g., shutdown_event.set).
    """
    global _shutdown_callback
    _shutdown_callback = cb


def _run_production_server(port: int):
    try:
        from waitress import serve
    except ImportError:
        print("[Web] waitress not installed; falling back to Flask dev server.")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
        return
    serve(app, host="127.0.0.1", port=port, threads=8, _quiet=True)


def start_web_server(port=DEFAULT_PORT, db_path=None, background=True):
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    if background:
        t = threading.Thread(target=lambda: _run_production_server(port), daemon=True, name="web-server")
        t.start()
        print(f"[Web] Dashboard running at http://127.0.0.1:{port}")
        return t
    else:
        print(f"[Web] Dashboard running at http://127.0.0.1:{port}")
        _run_production_server(port)


def main():
    parser = argparse.ArgumentParser(description="Bazaar Tracker Web Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", type=str, default=None, help="Path to bazaar_runs.db")
    args = parser.parse_args()
    start_web_server(port=args.port, db_path=args.db, background=False)


if __name__ == "__main__":
    main()
