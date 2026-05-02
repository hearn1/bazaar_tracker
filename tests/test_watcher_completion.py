import sys
import types

import db
import watcher


def test_run_completion_flushes_without_bridge_or_scorer(monkeypatch):
    calls = []

    fake_bridge = types.SimpleNamespace(
        enrich_run=lambda run_id: calls.append(("bridge", run_id))
    )
    fake_scorer = types.SimpleNamespace(
        score_run=lambda run_id: calls.append(("score", run_id)),
        print_report=lambda scored, run_id: calls.append(("report", run_id)),
    )
    monkeypatch.setitem(sys.modules, "bridge", fake_bridge)
    monkeypatch.setitem(sys.modules, "scorer", fake_scorer)
    monkeypatch.setattr(db, "flush", lambda: calls.append(("flush", None)))

    handler = watcher.build_run_complete_handler()
    handler({"run_id": 42, "hero": "Karnok"})

    assert calls == [("flush", None)]

