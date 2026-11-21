"""Forward paper tournament: frozen in advance, forward bars only, fails closed."""

import pytest

from ai_trader.research import data as rd
from ai_trader.research import tournament as tm

FULL = rd.synthetic(1400)
STEP = 86400


def _reg(n=1200):
    return tm.register({"SYN": FULL.prefix(n)})


def _now(s):
    return s.ts[-1] + STEP + 60


def test_registration_cannot_see_forward_bars():
    reg = _reg()
    assert reg["data"]["SYN"]["first_forward_ts"] == FULL.ts[1200]
    rows = list(zip(FULL.ts, FULL.open, FULL.high, FULL.low, FULL.close, FULL.volume))
    other = rd._from_rows("SYN", "1d", rows[:1200] + [(t, o * 3, h * 3, l * 3, c * 3, v) for t, o, h, l, c, v in rows[1200:]])
    assert tm.register({"SYN": other.prefix(1200)})["entries"] == reg["entries"]
    assert len(reg["entries"]) == len(tm.CANDIDATES) + 1


def test_board_scores_forward_bars_only_and_is_repeatable():
    reg = _reg()
    b0 = tm.board(reg, {"SYN": FULL.prefix(1200)}, now=_now(FULL.prefix(1200)))
    assert all(r["forward_bars"] == 0 and r["equity"] == 1.0 for r in b0["rows"]) and b0["provisional"]
    b1 = tm.board(reg, {"SYN": FULL}, now=_now(FULL))
    b2 = tm.board(reg, {"SYN": FULL}, now=_now(FULL))
    assert b1["rows"] == b2["rows"]  # recomputing never duplicates or loses a paper trade
    hold = next(r for r in b1["rows"] if r["benchmark"])
    assert hold["forward_bars"] == 200
    gross = FULL.close[-1] / FULL.open[1200]
    assert hold["equity"] == pytest.approx(gross * (1 - 0.0025) / 1.0005)
    assert "Not trading profits" in tm.markdown(b1)


def test_short_or_losing_runs_never_pass_the_paper_gate():
    b = tm.board(_reg(1380), {"SYN": FULL}, now=_now(FULL))
    assert not any(r["paper_gate"]["passed"] for r in b["rows"])
    b = tm.board(_reg(), {"SYN": FULL}, now=_now(FULL))
    for r in b["rows"]:
        if r["paper_gate"]["passed"]:
            assert r["net_return"] > 0 and r["trades"] >= 20 and r["max_drawdown"] <= 0.15


def test_fails_closed_on_stale_revised_or_missing_data():
    reg = _reg()
    with pytest.raises(tm.TournamentError, match="stale"):
        tm.board(reg, {"SYN": FULL}, now=_now(FULL) + 3 * STEP)
    rows = list(zip(FULL.ts, FULL.open, FULL.high, FULL.low, FULL.close, FULL.volume))
    t, o, h, l, c, v = rows[50]
    rows[50] = (t, o, h * 1.01, l, c, v)
    with pytest.raises(tm.TournamentError, match="hash"):
        tm.board(reg, {"SYN": rd._from_rows("SYN", "1d", rows)}, now=_now(FULL))
    with pytest.raises(tm.TournamentError):
        tm.board(reg, {}, now=_now(FULL))


def test_registration_is_never_overwritten(tmp_path):
    p = tmp_path / "reg.json"
    tm.save_registration(_reg(), p)
    with pytest.raises(tm.TournamentError):
        tm.save_registration(_reg(), p)
    assert tm.load_registration(p)["simulated"] is True


def test_live_board_caches_and_reports_failure_without_stale_rows(tmp_path):
    p = tmp_path / "reg.json"
    tm.save_registration(_reg(), p)
    tm._reset_cache()
    calls = []

    def fetch(sym, interval):
        calls.append(sym)
        return FULL

    now = _now(FULL)
    assert tm.live_board(fetch=fetch, now=now, path=p)["ok"] is True
    assert tm.live_board(fetch=fetch, now=now + 10, path=p)["ok"] is True and len(calls) == 1

    def broken(sym, interval):
        raise OSError("network down")

    out = tm.live_board(fetch=broken, now=now + tm.REFRESH_SECONDS + 1, path=p)
    assert out["ok"] is False and out["rows"] == [] and "network down" in out["error"]
    assert out["live"] is False
    tm._reset_cache()


def test_http_route_is_a_read(monkeypatch):
    from fastapi.testclient import TestClient

    from ai_trader import http_api
    from ai_trader.rpc import MUTATING_COMMANDS, READ_COMMANDS

    assert "tournament" in READ_COMMANDS and "tournament" not in MUTATING_COMMANDS
    monkeypatch.setattr(tm, "live_board", lambda: {"ok": True, "simulated": True, "live": False, "rows": []})
    r = TestClient(http_api.create_app()).get("/api/tournament")
    assert r.status_code == 200 and r.json()["live"] is False
