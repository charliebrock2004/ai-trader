import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "health_check", Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

NOW = 1_800_000_000.0
HEALTH = {"ok": True, "live": False, "live_trading_allowed": False}


def _board(**over):
    b = {
        "ok": True,
        "simulated": True,
        "live": False,
        "rows": [{"id": "x"}],
        "data": {"BTCUSDT": {"last_bar": NOW - 86400}, "ETHUSDT": {"last_bar": NOW - 86400}},
    }
    b.update(over)
    return b


def test_healthy_replies_have_no_problems():
    assert hc.assess(HEALTH, _board(), now=NOW) == []


def test_missing_replies_are_reported():
    assert hc.assess(None, None, now=NOW) == [
        "worker /health: no valid reply",
        "site /api/tournament: no valid reply",
    ]


def test_any_live_marker_is_a_safety_problem_listed_first():
    p = hc.assess({**HEALTH, "live_trading_allowed": True}, _board(ok=False, error="x"), now=NOW)
    assert p[0].startswith("SAFETY") and "leaderboard: x" in p
    assert hc.assess({**HEALTH, "live": True}, _board(), now=NOW)[0].startswith("SAFETY")
    assert hc.assess(HEALTH, _board(live=True), now=NOW)[0].startswith("SAFETY")
    assert hc.assess(HEALTH, _board(simulated=False), now=NOW)[0].startswith("SAFETY")
    # A missing marker is not assumed safe.
    assert hc.assess({"ok": True, "live": False}, _board(), now=NOW)[0].startswith("SAFETY")


def test_failed_board_reports_its_own_error():
    assert hc.assess(HEALTH, {"ok": False, "live": False, "error": "stale candles"}, now=NOW) == [
        "leaderboard: stale candles"
    ]


def test_stale_empty_and_disagreeing_data():
    old = {"BTCUSDT": {"last_bar": NOW - 4 * 86400}}
    assert any("old" in p for p in hc.assess(HEALTH, _board(data=old), now=NOW))
    assert "leaderboard: no rows" in hc.assess(HEALTH, _board(rows=[]), now=NOW)
    split = {"BTCUSDT": {"last_bar": NOW - 86400}, "ETHUSDT": {"last_bar": NOW - 2 * 86400}}
    assert any("disagree" in p for p in hc.assess(HEALTH, _board(data=split), now=NOW))
