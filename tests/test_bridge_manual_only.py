import pytest

import bridge


def test_bridge_score_cli_option_removed(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bridge.py", "--score"])
    with pytest.raises(SystemExit) as exc:
        bridge.main()
    assert exc.value.code != 0

