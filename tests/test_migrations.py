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

REMOVED_COLUMNS = {
    "runs": {"api_time_start", "api_time_end"},
    "decisions": {"score_raw"},
    "combat_results": {"pvp_resolved"},
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
        for table, removed in REMOVED_COLUMNS.items():
            assert removed.isdisjoint(_columns(conn, table))
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
        for table, removed in REMOVED_COLUMNS.items():
            assert removed.isdisjoint(_columns(conn, table))
    finally:
        conn.close()


def test_migrates_v2_shape_drops_retired_columns_and_preserves_live_context(tmp_path, monkeypatch):
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
                raw_log_path TEXT,
                api_time_start TEXT,
                api_time_end TEXT
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
                score_notes TEXT,
                board_snapshot_json TEXT,
                offered_names TEXT,
                offered_templates TEXT,
                day INTEGER,
                hour INTEGER,
                gold INTEGER,
                health INTEGER,
                health_max INTEGER,
                api_game_state_id INTEGER,
                phase_actual TEXT
            );
            CREATE TABLE combat_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES runs(id),
                timestamp TEXT,
                outcome TEXT,
                combat_type TEXT DEFAULT 'pve',
                duration_secs REAL,
                player_board TEXT,
                opponent_board TEXT,
                pvp_resolved INTEGER DEFAULT 0
            );
            PRAGMA user_version = 2;
            """
        )
        conn.execute(
            """
            INSERT INTO runs
                (session_id, account_id, hero, started_at, raw_log_path, api_time_start, api_time_end)
            VALUES ('s1', 'a1', 'Karnok', '10:00', 'Player.log', 'old-start', 'old-end')
            """
        )
        conn.execute(
            """
            INSERT INTO decisions
                (run_id, decision_seq, timestamp, game_state, decision_type, offered,
                 chosen_id, chosen_template, rejected, board_section, target_socket,
                 score_raw, score_label, score_notes, board_snapshot_json,
                 offered_names, offered_templates, day, hour, gold, health,
                 health_max, api_game_state_id, phase_actual)
            VALUES
                (1, 1, '10:01', 'EncounterState', 'item', '["itm_a"]',
                 'itm_a', 'T_A', '[]', 'Player', 'S1',
                 0.7, 'good', 'live', '{}',
                 '["Cool Item"]', '{"itm_a":"T_A"}', 8, 2, 12, 45,
                 60, 99, 'late')
            """
        )
        conn.execute(
            """
            INSERT INTO combat_results
                (run_id, timestamp, outcome, combat_type, duration_secs, player_board, opponent_board, pvp_resolved)
            VALUES (1, '10:02', 'opponent_died', 'pvp', 12.5, '[]', '[]', 1)
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.init_db()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        for table, removed in REMOVED_COLUMNS.items():
            assert removed.isdisjoint(_columns(conn, table))
        d = conn.execute("SELECT * FROM decisions WHERE id=1").fetchone()
        assert d["offered_names"] == '["Cool Item"]'
        assert d["offered_templates"] == '{"itm_a":"T_A"}'
        assert d["day"] == 8
        assert d["hour"] == 2
        assert d["gold"] == 12
        assert d["health"] == 45
        assert d["health_max"] == 60
        assert d["api_game_state_id"] == 99
        assert d["phase_actual"] == "late"
    finally:
        conn.close()
