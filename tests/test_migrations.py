import sqlite3

import db


REQUIRED_COLUMNS = {
    "runs": {"api_time_start", "api_time_end"},
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
    "combat_results": {"combat_type", "pvp_resolved"},
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


def test_migrates_legacy_v1_shape_to_current(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE,
                account_id TEXT,
                hero TEXT,
                started_at TEXT,
                ended_at TEXT,
                outcome TEXT,
                raw_log_path TEXT
            );
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id),
                decision_seq INTEGER,
                timestamp TEXT,
                game_state TEXT,
                decision_type TEXT,
                offered TEXT,
                chosen_id TEXT,
                chosen_template TEXT,
                rejected TEXT,
                board_section TEXT,
                target_socket TEXT,
                score_raw REAL,
                score_label TEXT,
                score_notes TEXT
            );
            CREATE TABLE combat_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id),
                timestamp TEXT,
                outcome TEXT,
                combat_type TEXT DEFAULT 'pve',
                duration_secs REAL,
                player_board TEXT,
                opponent_board TEXT
            );
            CREATE TABLE card_cache (
                template_id TEXT PRIMARY KEY,
                name TEXT,
                card_type TEXT,
                tier TEXT,
                tags TEXT,
                raw_json TEXT,
                cached_at TEXT
            );
            PRAGMA user_version = 1;
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.init_db()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert REQUIRED_TABLES.issubset(_tables(conn))
        for table, required in REQUIRED_COLUMNS.items():
            assert required.issubset(_columns(conn, table))
    finally:
        conn.close()
