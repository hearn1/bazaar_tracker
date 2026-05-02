import json
import sqlite3

import db
import run_state
import scorer
from run_state import RunState


class PhaseRecordingScorer:
    phases = []

    def __init__(self, hero, conn):
        pass

    def score_decision(self, decision, decision_id):
        phase = scorer.detect_phase(
            decision.get("decision_seq") or 0,
            0,
            day=decision.get("day"),
            phase_actual=decision.get("phase_actual"),
        )
        self.__class__.phases.append(phase)
        db.update_decision_score(decision_id, phase, f"phase={phase}")
        return {"label": phase, "notes": f"phase={phase}"}

    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def test_live_scorer_uses_live_day_phase_context(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", PhaseRecordingScorer)
    PhaseRecordingScorer.phases = []

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO card_cache (template_id, name, card_type, tier, tags, raw_json, cached_at)
            VALUES ('T_A', 'Cool Item', 'Item', 'A', '[]', '{}', 'now')
            """
        )
        gs_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-01T12:00:00+00:00', 'EncounterState', 'Karnok', 9, 1, 20, 50, 60)
            RETURNING id
            """
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) VALUES (?, 'itm_a', 'T_A', 'offered')",
            (gs_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-ctx"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-ctx"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    assert PhaseRecordingScorer.phases == ["late"]
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT score_label, score_notes, offered_names FROM decisions").fetchone()
        assert d["score_label"] == "late"
        assert d["score_notes"] == "phase=late"
        assert json.loads(d["offered_names"]) == ["Cool Item"]
    finally:
        conn.close()

