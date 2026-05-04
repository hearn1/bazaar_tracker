"""
Tests for shop-buffer grouping logic in build_overlay_review_rows.

Verifies that:
- Two consecutive item decisions with identical rejected JSON → one missed row
  emitted at buffer close, not two.
- An item decision followed by a skip → buffer flushes before skip is processed,
  producing two separate missed rows.
- rejected=[] → no missed row emitted.
- A skip with build-relevant resolved_offered → one missed row.

_pick_best_review_match is mocked so tests do not depend on live build catalogs.
"""

import json
import sqlite3

import db
import scorer
import web.review_builder as rb


_FIXED_MATCH = {
    "item_name": "Sword",
    "arch_name": "Bleed Build",
    "bucket": "core",
    "kind": "enable",
    "rank": (1, 3, 1, 1, 0, 0, 1, 1, 0, 1),
}


def _make_decision(seq, dtype, chosen=None, rejected=None, offered=None):
    offered = offered or []
    rejected = rejected if rejected is not None else []
    return {
        "id": seq,
        "decision_seq": seq,
        "decision_type": dtype,
        "game_state": "EncounterState",
        "board_section": "Player",
        "chosen_id": f"itm_{seq}" if chosen else None,
        "chosen_template": f"T_{seq}" if chosen else None,
        "chosen_name": chosen,
        "offered": json.dumps(offered),
        "offered_raw": offered,
        "offered_names": json.dumps(offered),
        "rejected": json.dumps(rejected),
        "score_label": None,
        "score_notes": None,
    }


def _fake_resolve_names(conn, decision, *, resolve_fn, safe_json_fn):
    offered_raw = safe_json_fn(decision.get("offered") or "[]") or []
    rejected_raw = safe_json_fn(decision.get("rejected") or "[]") or []
    return {
        "chosen_template": decision.get("chosen_template") or "",
        "chosen_name": decision.get("chosen_name"),
        "offered_raw": offered_raw,
        "offered_names": offered_raw,
        "rejected_names": rejected_raw,
        "resolved_offered": [n for n in offered_raw if isinstance(n, str)],
        "resolved_rejected": [n for n in rejected_raw if isinstance(n, str)],
    }


_DEFAULT_BUILD_DATA = {"game_phases": {"late": {"archetypes": []}}}


def _run(monkeypatch, tmp_path, decisions, pick_fn, build_data=None):
    db_path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.close_shared_conn()
    db.init_db()

    monkeypatch.setattr(scorer, "_load_board_snapshot_map", lambda conn, run_id: {})
    monkeypatch.setattr(scorer, "find_committed_archetype", lambda board_names, build_data: (None, None))
    monkeypatch.setattr(rb, "resolve_overlay_decision_names", _fake_resolve_names)
    monkeypatch.setattr(rb, "_pick_best_review_match", pick_fn)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return rb.build_overlay_review_rows(
            conn,
            run_id=1,
            decisions=decisions,
            build_data=build_data if build_data is not None else _DEFAULT_BUILD_DATA,
            resolve_fn=lambda conn, t: t,
            safe_json_fn=json.loads,
        )
    finally:
        conn.close()


def _pick_for_names(*names):
    """Return a match (with item_name echoed from the input) when any of `names` appears."""
    def _fn(item_names, board_names, archetypes):
        for n in (item_names or []):
            if n in names:
                match = dict(_FIXED_MATCH)
                match["item_name"] = n
                return match
        return None
    return _fn


# ── Test cases ────────────────────────────────────────────────────────────────

def test_two_items_identical_rejected_emits_one_missed_row(monkeypatch, tmp_path):
    """Two purchases from the same shop (identical rejected) → exactly one missed row."""
    decisions = [
        _make_decision(1, "item", chosen="Item A", rejected=["Sword"], offered=["Item A", "Sword"]),
        _make_decision(2, "item", chosen="Item B", rejected=["Sword"], offered=["Item B", "Sword"]),
    ]
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=_pick_for_names("Sword"))

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    # Anchored to the last item in the buffer (seq 2).
    assert missed[0]["decision_seq"] == 2


def test_item_then_skip_flushes_buffer_first(monkeypatch, tmp_path):
    """Item (shop 1) followed by skip → buffer flush fires before skip, producing two missed rows."""
    decisions = [
        _make_decision(1, "item", chosen="Item A", rejected=["Sword"], offered=["Item A", "Sword"]),
        _make_decision(2, "skip", offered=["Bow"]),
    ]
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=_pick_for_names("Sword", "Bow"))

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 2
    seqs = {r["decision_seq"] for r in missed}
    assert 1 in seqs  # buffer flush, anchored to item_A
    assert 2 in seqs  # skip row


def test_empty_rejected_no_missed_row(monkeypatch, tmp_path):
    """rejected=[] means everything was purchased → no missed row should fire."""
    decisions = [
        _make_decision(1, "item", chosen="Item A", rejected=[], offered=["Item A"]),
    ]
    # Mock always returns a match — but empty rejected should short-circuit before calling it.
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=lambda *a: _FIXED_MATCH)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 0


def test_economy_item_in_rejected_emits_missed_row(monkeypatch, tmp_path):
    """Passing on an economy_item (not in any archetype) still emits a missed row."""
    decisions = [
        _make_decision(1, "item", chosen="Item A", rejected=["Hunter's Journal"],
                       offered=["Item A", "Hunter's Journal"]),
    ]
    # Archetype check always returns None — economy fallback must carry it.
    build_data = {
        "game_phases": {
            "early": {"archetypes": [], "economy_items": ["Hunter's Journal"],
                      "universal_utility_items": []},
            "late": {"archetypes": []},
        }
    }
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=lambda *a: None,
                build_data=build_data)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["review_title"] == "Hunter's Journal"
    assert missed[0]["review_kind"] == "economy"


def test_missed_row_suppressed_when_item_acquired_later(monkeypatch, tmp_path):
    """Missed row is suppressed if the item was acquired on a subsequent decision."""
    decisions = [
        # Shop: bought Tinderbox, passed on Hunter's Sled → missed row would fire at flush.
        _make_decision(38, "item", chosen="Tinderbox", rejected=["Hunter's Sled"],
                       offered=["Tinderbox", "Hunter's Sled"]),
        # Free reward: Hunter's Sled received immediately after.
        _make_decision(39, "free_reward", chosen="Hunter's Sled",
                       offered=["Hunter's Sled"]),
    ]
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=_pick_for_names("Hunter's Sled"))

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 0


def test_skip_with_relevant_offered_emits_one_missed_row(monkeypatch, tmp_path):
    """A skip whose resolved_offered contains a build-relevant item → one missed row."""
    decisions = [
        _make_decision(1, "skip", offered=["Sword"]),
    ]
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=_pick_for_names("Sword"))

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["decision_seq"] == 1
