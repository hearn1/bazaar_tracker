import sqlite3

import db


REQUIRED_COLUMNS = {
    "runs": {"session_id", "account_id", "hero", "started_at", "ended_at", "outcome", "raw_log_path"},
    "decisions": {
        "board_snapshot_json",
        "offered_names",
        "offered_templates",
        "day",
        "hour",
        "gold",
        "health",
        "health_max",
        "api_game_state_id",
        "phase_actual",
    },
    "combat_results": {"combat_type"},
}

REQUIRED_TABLES = {
    "runs",
    "decisions",
    "combat_results",
    "card_cache",
    "api_messages",
    "api_game_states",
    "api_cards",
    "api_player_attrs",
}


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def test_init_db_creates_latest_schema(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)

    db.init_db()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert REQUIRED_TABLES.issubset(_tables(conn))
        for table, required in REQUIRED_COLUMNS.items():
            assert required.issubset(_columns(conn, table))
    finally:
        conn.close()
