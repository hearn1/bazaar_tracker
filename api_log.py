# -*- coding: utf-8 -*-
"""
api_log.py — Stores and queries captured API traffic in SQLite.

This is the bridge between the capture layer (capture_launcher.py or
capture_frida.py) and the analysis layer (run_state.py, scorer.py).

It provides:
  1. A persistent log of all API requests and responses
  2. Queryable game state at any point in a run
  3. Template ID resolution for rejected items (the biggest scoring gap)
  4. Gold/HP tracking over time
  5. Definitive PvP win/loss resolution

DATABASE SCHEMA
===============
  api_messages     — raw captured request/response pairs
  api_game_states  — parsed GameStateSnapshotDTO per response
  api_cards        — all cards seen in each game state snapshot
  api_player_attrs — player attributes (gold, HP, etc.) per snapshot

USAGE
=====
  # Import captured data from Frida/tshark output
  python api_log.py --import captures/frida_20260416_120000/

  # Import a single msgpack binary
  python api_log.py --import-bin captures/response_001.bin

  # Query game state at a specific request ID
  python api_log.py --state-at 42

  # Show all offered items with template IDs (resolves the scoring gap)
  python api_log.py --offered --run-id 5

  # Replay captured API data through run_state.py
  python api_log.py --replay --run-id 5

  # Export gold/HP timeline for a run
  python api_log.py --timeline --run-id 5
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import db

_api_tables_initialized = False


def get_conn() -> sqlite3.Connection:
    """Return a standalone DB connection via the shared tracker helper."""
    return db.get_conn()


def init_api_tables():
    """Ensure API capture tables exist via the central DB migration path."""
    global _api_tables_initialized
    if _api_tables_initialized:
        return

    db.ensure_schema()
    _api_tables_initialized = True
    print("[ApiLog] API capture tables initialized.")


def store_raw_message(direction: str, raw_bytes: bytes, rid: str = None,
                      session_id: str = None, headers: dict = None,
                      http_status: int = None) -> int:
    """Store a raw captured message and attempt to decode it."""
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # Attempt msgpack decode
    decoded_json = None
    try:
        from msgpack_decoder import decode_raw
        raw_objects = decode_raw(raw_bytes)
        if raw_objects:
            decoded_json = json.dumps(raw_objects, default=str)
    except Exception:
        pass

    content_type = (headers or {}).get("content-type", "application/msgpack")

    cur = conn.execute("""
        INSERT INTO api_messages (captured_at, direction, rid, session_id,
                                  raw_msgpack, decoded_json, http_status,
                                  content_type, headers_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, (now, direction, rid, session_id, raw_bytes, decoded_json,
          http_status, content_type, json.dumps(headers) if headers else None))

    msg_id = cur.fetchone()[0]
    conn.commit()

    # If this is a response, try to parse and store the game state
    if direction == "response" and decoded_json:
        try:
            from msgpack_decoder import decode_raw, parse_game_state
            raw_objects = decode_raw(raw_bytes)
            if raw_objects:
                gs = parse_game_state(raw_objects)
                if gs:
                    _store_game_state(conn, msg_id, now, gs)
        except Exception as e:
            print(f"[ApiLog] Game state parse failed for msg {msg_id}: {e}")

    conn.close()
    return msg_id


def _store_game_state(conn: sqlite3.Connection, message_id: int,
                      captured_at: str, gs: dict):
    """Store a parsed game state snapshot and its cards."""
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})

    cur = conn.execute("""
        INSERT INTO api_game_states
            (message_id, captured_at, run_state, hero, day, hour,
             victories, defeats, gold, health, health_max, level,
             data_version, offered_count, board_count, stash_count,
             skills_count, opponent_count, selection_set,
             reroll_cost, rerolls_remaining, full_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, (
        message_id, captured_at,
        state.get("state"),
        player.get("hero"),
        run.get("day"),
        run.get("hour"),
        run.get("victories"),
        run.get("defeats"),
        player.get("Gold"),
        player.get("Health"),
        player.get("HealthMax"),
        player.get("Level"),
        run.get("data_version"),
        len(gs.get("offered", [])),
        len(gs.get("player_board", [])),
        len(gs.get("player_stash", [])),
        len(gs.get("player_skills", [])),
        len(gs.get("opponent_board", [])),
        json.dumps(state.get("selection_set", [])),
        state.get("reroll_cost"),
        state.get("rerolls_remaining"),
        json.dumps(gs, default=str),
    ))
    gs_id = cur.fetchone()[0]

    # Store all cards with their categories
    categories = {
        "offered": gs.get("offered", []),
        "player_board": gs.get("player_board", []),
        "player_stash": gs.get("player_stash", []),
        "player_skills": gs.get("player_skills", []),
        "opponent_board": gs.get("opponent_board", []),
    }

    for category, cards in categories.items():
        for card in cards:
            conn.execute("""
                INSERT INTO api_cards
                    (game_state_id, instance_id, template_id, card_type,
                     tier, size, owner, section, socket, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                gs_id,
                card.get("instance_id"),
                card.get("template_id"),
                card.get("type"),
                card.get("tier"),
                card.get("size"),
                card.get("owner"),
                card.get("section"),
                card.get("socket"),
                category,
            ))

    conn.commit()


def query_offered_at(game_state_id: int) -> list[dict]:
    """Get all offered cards at a specific game state snapshot."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT instance_id, template_id, card_type, tier, size
        FROM api_cards
        WHERE game_state_id = ? AND category = 'offered'
    """, (game_state_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_offered_with_names(game_state_id: int) -> list[dict]:
    """Get offered cards with resolved names from card_cache."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ac.instance_id, ac.template_id, ac.card_type, ac.tier, ac.size,
               cc.name as card_name
        FROM api_cards ac
        LEFT JOIN card_cache cc ON ac.template_id = cc.template_id
        WHERE ac.game_state_id = ? AND ac.category = 'offered'
    """, (game_state_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_gold_hp_timeline() -> list[dict]:
    """Get gold and HP over time for analysis."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT captured_at, run_state, gold, health, health_max,
               victories, defeats, offered_count, board_count
        FROM api_game_states
        ORDER BY id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def print_game_states():
    """Print summary of all captured game states."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, captured_at, run_state, hero, day, hour,
               gold, health, health_max, victories, defeats,
               offered_count, board_count, skills_count
        FROM api_game_states
        ORDER BY id
    """).fetchall()
    conn.close()

    if not rows:
        print("[ApiLog] No game states captured yet.")
        return

    print(f"\n{'='*80}")
    print(f"  CAPTURED GAME STATES ({len(rows)} snapshots)")
    print(f"{'='*80}")

    for r in rows:
        print(f"  #{r['id']:>3}  {r['run_state']:<16}  "
              f"Day {r['day'] or '?'}  "
              f"Gold={r['gold'] or '?'}  "
              f"HP={r['health'] or '?'}/{r['health_max'] or '?'}  "
              f"PvP={r['victories'] or 0}W/{r['defeats'] or 0}L  "
              f"offered={r['offered_count']}  board={r['board_count']}")

    print(f"{'='*80}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bazaar API capture log")
    parser.add_argument("--init", action="store_true", help="Initialize API tables")
    parser.add_argument("--states", action="store_true", help="Show all captured game states")
    parser.add_argument("--offered", type=int, help="Show offered cards at game_state_id")
    parser.add_argument("--timeline", action="store_true", help="Show gold/HP timeline")
    parser.add_argument("--run-id", type=int, help="Filter to specific run ID")
    args = parser.parse_args()

    if args.init:
        init_api_tables()
    elif args.states:
        print_game_states()
    elif args.offered is not None:
        cards = query_offered_with_names(args.offered)
        for c in cards:
            name = c.get("card_name") or c.get("template_id", "?")
            print(f"  {c['instance_id']:<18}  {name:<30}  {c['tier']}  {c['card_type']}  {c['size']}")
    elif args.timeline:
        timeline = query_gold_hp_timeline()
        for t in timeline:
            print(f"  {t['run_state']:<16}  Gold={t['gold'] or '?':>4}  "
                  f"HP={t['health'] or '?':>3}/{t['health_max'] or '?':>3}  "
                  f"PvP={t['victories'] or 0}W/{t['defeats'] or 0}L  "
                  f"offered={t['offered_count']}")
    else:
        parser.print_help()
