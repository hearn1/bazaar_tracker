"""
db.py — SQLite schema and helpers for Bazaar run tracker.

Performance notes:
  - A single shared connection is reused across the session to avoid
    repeated open/close/PRAGMA overhead during live gameplay.
  - PRAGMA synchronous=NORMAL is safe in WAL mode — only risks data
    loss on OS crash (not app crash). Eliminates per-commit fsync.
  - A background writer thread handles all live gameplay writes so the
    watcher thread (which processes Player.log lines) never blocks on
    disk I/O. Writes that need a return value (insert_decision) use a
    one-shot Future; fire-and-forget writes (insert_combat,
    update_decision_rejected) are fully async.
  - flush() drains the write queue and commits. Call at natural breakpoints.
  - get_conn() still works for one-off scripts that need their own connection.
"""

import sqlite3
import json
import queue
import threading
import time
from typing import Optional

import app_paths

DB_PATH = app_paths.db_path()
SCHEMA_VERSION = 3

# Shared connection for the live session (only touched by the writer thread
# once start_writer() is called).
_shared_conn: Optional[sqlite3.Connection] = None

# Background writer
_write_queue: Optional[queue.Queue] = None
_write_thread: Optional[threading.Thread] = None
_writer_thread_id: Optional[int] = None
_writer_ready: Optional[threading.Event] = None

def get_conn() -> sqlite3.Connection:
    """Return a new standalone connection. Use for one-off scripts or threads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_shared_conn() -> sqlite3.Connection:
    """Return the shared live-session connection.

    When the background writer is running, this connection may only be used
    from the writer thread. Other threads must use get_conn().
    """
    global _shared_conn, _write_queue, _writer_thread_id

    if _write_queue is not None:
        current_id = threading.get_ident()
        if _writer_thread_id is not None and current_id != _writer_thread_id:
            raise RuntimeError(
                "get_shared_conn() called from a non-writer thread while the "
                "background writer is active. Use get_conn() instead."
            )

    if _shared_conn is None:
        _shared_conn = get_conn()
    return _shared_conn


# ── Background writer ─────────────────────────────────────────────────────────

def _writer_loop():
    """Background thread: owns the shared connection and executes queued work."""
    global _shared_conn, _writer_thread_id, _writer_ready

    _writer_thread_id = threading.get_ident()
    if _shared_conn is None:
        _shared_conn = get_conn()

    if _writer_ready is not None:
        _writer_ready.set()

    while True:
        item = _write_queue.get()
        if item is None:
            if _shared_conn is not None:
                try:
                    _shared_conn.commit()
                except Exception:
                    pass
            _write_queue.task_done()
            return

        func, args, kwargs, result_future = item
        try:
            result = func(*args, **kwargs)
            if result_future is not None:
                result_future.put(result)
        except Exception as e:
            print(f"[DB] Background write error: {e}")
            if result_future is not None:
                result_future.put(e)
        finally:
            _write_queue.task_done()

def start_writer():
    """Start the background DB writer thread."""
    global _write_queue, _write_thread, _writer_ready
    if _write_queue is not None:
        return

    _write_queue = queue.Queue()
    _writer_ready = threading.Event()
    _write_thread = threading.Thread(
        target=_writer_loop,
        name="db-writer",
        daemon=True,
    )
    _write_thread.start()
    _writer_ready.wait()
    print("[DB] Background writer started.")

def stop_writer():
    """Drain the write queue, commit, and stop the writer thread."""
    global _write_queue, _write_thread, _writer_thread_id, _writer_ready
    if _write_queue is None:
        return
    _write_queue.put(None)
    _write_queue.join()
    if _write_thread is not None:
        _write_thread.join(timeout=5)
    _write_queue = None
    _write_thread = None
    _writer_thread_id = None
    _writer_ready = None


def _enqueue_fire_and_forget(func, *args, **kwargs):
    """Push a write onto the background queue. No return value."""
    if _write_queue is not None:
        _write_queue.put((func, args, kwargs, None))
    else:
        func(*args, **kwargs)


def _enqueue_with_result(func, *args, **kwargs):
    """Push a write onto the background queue and block for the result.

    This is used for insert_decision which returns the new row ID.
    The caller still doesn't block on disk I/O — only on the queue
    handoff, which is sub-microsecond.
    """
    if _write_queue is not None:
        future = queue.Queue(maxsize=1)
        _write_queue.put((func, args, kwargs, future))
        result = future.get()
        if isinstance(result, Exception):
            raise result
        return result
    else:
        return func(*args, **kwargs)


# ── Flush / commit ────────────────────────────────────────────────────────────
_last_flush_time: float = 0.0


def _flush_impl():
    """Commit on the writer thread."""
    global _last_flush_time
    if _shared_conn is not None:
        _shared_conn.commit()
        _last_flush_time = time.monotonic()


def flush():
    """Commit pending writes on the shared connection.
    Call at natural breakpoints: state transitions, combat end, run end.
    If the writer is running, this drains the queue first."""
    if _write_queue is not None:
        # Drain all pending writes, then commit
        _enqueue_fire_and_forget(_flush_impl)
        _write_queue.join()
    else:
        _flush_impl()


def flush_if_stale(max_age_secs: float = 2.0):
    """Commit if it's been more than max_age_secs since the last flush."""
    if time.monotonic() - _last_flush_time >= max_age_secs:
        flush()


def close_shared_conn():
    """Stop the writer and close the shared connection (call on shutdown)."""
    global _shared_conn

    stop_writer()

    if _shared_conn is not None:
        try:
            _shared_conn.commit()
            _shared_conn.close()
        except Exception:
            pass
        _shared_conn = None


def get_schema_version() -> int:
    """Return SQLite PRAGMA user_version for the configured DB."""
    conn = get_conn()
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _create_latest_tables(conn: sqlite3.Connection) -> None:
    """Create the latest schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT UNIQUE,
            account_id      TEXT,
            hero            TEXT,
            started_at      TEXT,
            ended_at        TEXT,
            outcome         TEXT,
            raw_log_path    TEXT
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              INTEGER REFERENCES runs(id),
            decision_seq        INTEGER,
            timestamp           TEXT,
            game_state          TEXT,
            decision_type       TEXT,
            offered             TEXT,
            chosen_id           TEXT,
            chosen_template     TEXT,
            rejected            TEXT,
            board_section       TEXT,
            target_socket       TEXT,
            score_label         TEXT,
            score_notes         TEXT DEFAULT '',
            board_snapshot_json TEXT,
            offered_names       TEXT,
            offered_templates   TEXT,
            day                 INTEGER,
            hour                INTEGER,
            gold                INTEGER,
            health              INTEGER,
            health_max          INTEGER,
            api_game_state_id   INTEGER,
            phase_actual        TEXT
        );

        CREATE TABLE IF NOT EXISTS combat_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER REFERENCES runs(id),
            timestamp       TEXT,
            outcome         TEXT,
            combat_type     TEXT DEFAULT 'pve',
            duration_secs   REAL,
            player_board    TEXT,
            opponent_board  TEXT
        );

        CREATE TABLE IF NOT EXISTS card_cache (
            template_id     TEXT PRIMARY KEY,
            name            TEXT,
            card_type       TEXT,
            tier            TEXT,
            tags            TEXT,
            raw_json        TEXT,
            cached_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS api_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at     TEXT NOT NULL,
            direction       TEXT NOT NULL,
            rid             TEXT,
            session_id      TEXT,
            raw_msgpack     BLOB,
            decoded_json    TEXT,
            http_status     INTEGER,
            content_type    TEXT,
            headers_json    TEXT
        );

        CREATE TABLE IF NOT EXISTS api_game_states (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id          INTEGER REFERENCES api_messages(id),
            captured_at         TEXT NOT NULL,
            run_state           TEXT,
            hero                TEXT,
            day                 INTEGER,
            hour                INTEGER,
            victories           INTEGER,
            defeats             INTEGER,
            gold                INTEGER,
            health              INTEGER,
            health_max          INTEGER,
            level               INTEGER,
            data_version        TEXT,
            offered_count       INTEGER DEFAULT 0,
            board_count         INTEGER DEFAULT 0,
            stash_count         INTEGER DEFAULT 0,
            skills_count        INTEGER DEFAULT 0,
            opponent_count      INTEGER DEFAULT 0,
            selection_set       TEXT,
            reroll_cost         INTEGER,
            rerolls_remaining   INTEGER,
            full_json           TEXT
        );

        CREATE TABLE IF NOT EXISTS api_cards (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            game_state_id   INTEGER REFERENCES api_game_states(id),
            instance_id     TEXT,
            template_id     TEXT,
            card_type       TEXT,
            tier            TEXT,
            size            TEXT,
            owner           TEXT,
            section         TEXT,
            socket          INTEGER,
            category        TEXT
        );

        CREATE TABLE IF NOT EXISTS api_player_attrs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            game_state_id   INTEGER REFERENCES api_game_states(id),
            attr_name       TEXT,
            attr_value      REAL
        );
    """)
    _create_latest_indexes(conn)


def _create_latest_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_decisions_run_seq
            ON decisions(run_id, decision_seq);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_combat_run_ts
            ON combat_results(run_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_api_cards_template
            ON api_cards(template_id);
        CREATE INDEX IF NOT EXISTS idx_api_cards_instance
            ON api_cards(instance_id);
        CREATE INDEX IF NOT EXISTS idx_api_cards_category
            ON api_cards(game_state_id, category);
        CREATE INDEX IF NOT EXISTS idx_api_gs_state
            ON api_game_states(run_state);
        CREATE INDEX IF NOT EXISTS idx_api_gs_msg
            ON api_game_states(message_id);
        CREATE INDEX IF NOT EXISTS idx_api_gs_hero_time
            ON api_game_states(hero, captured_at);
        CREATE INDEX IF NOT EXISTS idx_api_gs_day_state
            ON api_game_states(day, run_state);
    """)


def migrate_db(conn: sqlite3.Connection) -> int:
    """Ensure the latest SQLite schema exists and return its version."""
    current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current_version > SCHEMA_VERSION:
        print(
            f"[DB] WARNING: DB schema version {current_version} is newer "
            f"than app schema {SCHEMA_VERSION}."
        )
        return current_version

    _create_latest_tables(conn)
    _create_latest_indexes(conn)
    _set_schema_version(conn, SCHEMA_VERSION)
    conn.commit()
    return SCHEMA_VERSION


def ensure_schema(conn: Optional[sqlite3.Connection] = None) -> int:
    """Ensure the configured database or supplied connection is current."""
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        return migrate_db(conn)
    finally:
        if owns_conn:
            conn.close()


def init_db():
    schema_version = ensure_schema()
    print(f"[DB] Initialized at {DB_PATH} (schema v{schema_version})")


def upsert_run(session_id: str, account_id: str, hero: str, started_at: str, log_path: str) -> int:
    return _enqueue_with_result(
        _upsert_run_impl, session_id, account_id, hero, started_at, log_path,
    )


def _upsert_run_impl(session_id: str, account_id: str, hero: str, started_at: str, log_path: str) -> int:
    conn = get_shared_conn()
    cur = conn.execute("""
        INSERT INTO runs (session_id, account_id, hero, started_at, raw_log_path)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            account_id=excluded.account_id,
            hero=excluded.hero
        RETURNING id
    """, (session_id, account_id, hero, started_at, log_path))
    run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def close_run(run_id: int, ended_at: str, outcome: str):
    _enqueue_fire_and_forget(_close_run_impl, run_id, ended_at, outcome)


def _close_run_impl(run_id: int, ended_at: str, outcome: str):
    conn = get_shared_conn()
    conn.execute("UPDATE runs SET ended_at=?, outcome=? WHERE id=?",
                 (ended_at, outcome, run_id))
    conn.commit()


def insert_decision(run_id: int, seq: int, timestamp: str, game_state: str,
                    decision_type: str, offered: list, chosen_id: str,
                    chosen_template: str, rejected: list,
                    board_section: str, target_socket: str,
                    score_notes: str = "",
                    board_snapshot_json: str = "",
                    *,
                    offered_names: Optional[list] = None,
                    offered_templates: Optional[dict] = None,
                    day: Optional[int] = None,
                    hour: Optional[int] = None,
                    gold: Optional[int] = None,
                    health: Optional[int] = None,
                    health_max: Optional[int] = None,
                    api_game_state_id: Optional[int] = None,
                    phase_actual: Optional[str] = None) -> int:
    return _enqueue_with_result(
        _insert_decision_impl,
        run_id, seq, timestamp, game_state, decision_type, offered,
        chosen_id, chosen_template, rejected, board_section, target_socket,
        score_notes, board_snapshot_json,
        offered_names=offered_names,
        offered_templates=offered_templates,
        day=day,
        hour=hour,
        gold=gold,
        health=health,
        health_max=health_max,
        api_game_state_id=api_game_state_id,
        phase_actual=phase_actual,
    )


def _insert_decision_impl(run_id: int, seq: int, timestamp: str, game_state: str,
                           decision_type: str, offered: list, chosen_id: str,
                           chosen_template: str, rejected: list,
                           board_section: str, target_socket: str,
                           score_notes: str = "",
                           board_snapshot_json: str = "",
                           *,
                           offered_names: Optional[list] = None,
                           offered_templates: Optional[dict] = None,
                           day: Optional[int] = None,
                           hour: Optional[int] = None,
                           gold: Optional[int] = None,
                           health: Optional[int] = None,
                           health_max: Optional[int] = None,
                           api_game_state_id: Optional[int] = None,
                           phase_actual: Optional[str] = None) -> int:
    conn = get_shared_conn()
    cur = conn.execute("""
        INSERT INTO decisions
            (run_id, decision_seq, timestamp, game_state, decision_type,
             offered, chosen_id, chosen_template, rejected, board_section, target_socket,
             score_notes, board_snapshot_json, offered_names, offered_templates,
             day, hour, gold, health, health_max, api_game_state_id, phase_actual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
    """, (run_id, seq, timestamp, game_state, decision_type,
          json.dumps(offered), chosen_id, chosen_template,
          json.dumps(rejected), board_section, target_socket, score_notes,
          board_snapshot_json or None,
          json.dumps(offered_names) if offered_names is not None else None,
          json.dumps(offered_templates) if offered_templates is not None else None,
          day, hour, gold, health, health_max, api_game_state_id, phase_actual))
    dec_id = cur.fetchone()[0]
    return dec_id


def update_decision_score(decision_id: int, label: Optional[str], notes: Optional[str]):
    """Write score_label and score_notes back to a decision row.

    Called by LiveScorer immediately after insert_decision so scores are
    available on the next overlay poll.
    Fire-and-forget — no return value needed.
    """
    _enqueue_fire_and_forget(_update_decision_score_impl, decision_id, label, notes)


def _update_decision_score_impl(decision_id: int, label: Optional[str], notes: Optional[str]):
    conn = get_shared_conn()
    conn.execute(
        "UPDATE decisions SET score_label = ?, score_notes = ? WHERE id = ?",
        (label, notes or "", decision_id),
    )


def update_decision_rejected(decision_id: int, rejected: list):
    _enqueue_fire_and_forget(_update_decision_rejected_impl, decision_id, rejected)


def _update_decision_rejected_impl(decision_id: int, rejected: list):
    conn = get_shared_conn()
    conn.execute("UPDATE decisions SET rejected=? WHERE id=?",
                 (json.dumps(rejected), decision_id))


def update_decision_purchase_details(
    decision_id: int,
    chosen_template: str,
    board_section: str,
    target_socket: str,
    chosen_id: str = "",
):
    _enqueue_fire_and_forget(
        _update_decision_purchase_details_impl,
        decision_id,
        chosen_template,
        board_section,
        target_socket,
        chosen_id,
    )


def _update_decision_purchase_details_impl(
    decision_id: int,
    chosen_template: str,
    board_section: str,
    target_socket: str,
    chosen_id: str = "",
):
    conn = get_shared_conn()
    conn.execute(
        """
        UPDATE decisions
        SET chosen_id = COALESCE(NULLIF(?, ''), chosen_id),
            chosen_template = COALESCE(NULLIF(?, ''), chosen_template),
            board_section = COALESCE(NULLIF(?, ''), board_section),
            target_socket = COALESCE(NULLIF(?, ''), target_socket)
        WHERE id = ?
        """,
        (chosen_id, chosen_template, board_section, target_socket, decision_id),
    )


def insert_combat(run_id: int, timestamp: str, outcome: str, combat_type: str,
                  duration_secs: float, player_board: list, opponent_board: list):
    _enqueue_fire_and_forget(
        _insert_combat_impl, run_id, timestamp, outcome, combat_type,
        duration_secs, player_board, opponent_board,
    )


def _insert_combat_impl(run_id: int, timestamp: str, outcome: str, combat_type: str,
                         duration_secs: float, player_board: list, opponent_board: list):
    conn = get_shared_conn()
    conn.execute("""
        INSERT OR IGNORE INTO combat_results
            (run_id, timestamp, outcome, combat_type, duration_secs, player_board, opponent_board)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (run_id, timestamp, outcome, combat_type, duration_secs,
          json.dumps(player_board), json.dumps(opponent_board)))


def lookup_card(template_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT name, card_type, tier, tags FROM card_cache WHERE template_id=?",
            (template_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def cache_cards(cards: list):
    """Bulk insert/replace card data from the static API."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        for card in cards:
            tid = (card.get("Id") or card.get("id") or
                   card.get("templateId") or card.get("TemplateId"))
            if not tid:
                continue
            name = (card.get("InternalName") or card.get("internalName") or
                    card.get("Name") or card.get("name") or
                    card.get("Localization", {}).get("Title", {}).get("Text") or
                    "Unknown")
            card_type = (card.get("$type") or card.get("Type") or
                         card.get("cardType") or card.get("CardType") or "")
            tier = (card.get("StartingTier") or card.get("Tier") or
                    card.get("tier") or "")
            conn.execute("""
                INSERT OR REPLACE INTO card_cache
                    (template_id, name, card_type, tier, tags, raw_json, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(tid), str(name), str(card_type), str(tier),
                json.dumps(card.get("Tags") or card.get("tags") or []),
                json.dumps(card),
                now,
            ))
        conn.commit()
    finally:
        conn.close()

    print(f"[CardCache] Cached {len(cards)} cards.")
