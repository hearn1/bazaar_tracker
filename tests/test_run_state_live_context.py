import json
import sqlite3

import db
import run_state
from run_state import RunState


class RecordingLiveScorer:
    calls = []

    def __init__(self, hero, conn):
        self.hero = hero

    def score_decision(self, decision, decision_id):
        self.__class__.calls.append((decision_id, dict(decision)))
        db.update_decision_score(decision_id, decision.get("phase_actual"), "scored live")
        return {"label": decision.get("phase_actual"), "notes": "scored live"}

    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _seed_card(conn, template_id, name):
    conn.execute(
        """
        INSERT INTO card_cache (template_id, name, card_type, tier, tags, raw_json, cached_at)
        VALUES (?, ?, 'Item', 'A', '[]', '{}', 'now')
        """,
        (template_id, name),
    )


def test_run_state_decision_insert_includes_live_context(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Cool Item")
        _seed_card(conn, "T_B", "Warm Item")
        gs_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-01T12:00:00+00:00', 'EncounterState', 'Karnok', 8, 2, 13, 44, 60)
            RETURNING id
            """
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT INTO api_cards (game_state_id, instance_id, template_id, category)
            VALUES (?, ?, ?, 'offered')
            """,
            [(gs_id, "itm_a", "T_A"), (gs_id, "itm_b", "T_B")],
        )
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-1"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert d["day"] == 8
        assert d["hour"] == 2
        assert d["gold"] == 13
        assert d["health"] == 44
        assert d["health_max"] == 60
        assert d["api_game_state_id"] == gs_id
        assert d["phase_actual"] == "late"
        assert json.loads(d["offered_names"]) == ["Cool Item", "Warm Item"]
        assert json.loads(d["offered_templates"]) == {"itm_a": "T_A", "itm_b": "T_B"}
        assert d["score_label"] == "late"
    finally:
        conn.close()

    assert RecordingLiveScorer.calls
    _decision_id, scored_decision = RecordingLiveScorer.calls[-1]
    assert scored_decision["day"] == 8
    assert scored_decision["phase_actual"] == "late"
    assert json.loads(scored_decision["offered_names"]) == ["Cool Item", "Warm Item"]

