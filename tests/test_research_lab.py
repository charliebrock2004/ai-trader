"""Research lab: no look-ahead, honest costs, and a gate that cannot be talked round."""

import math

import pytest

from ai_trader.research import backtest as bt
from ai_trader.research import data as rd
from ai_trader.research import metrics as mx
from ai_trader.research import qualification as q
from ai_trader.research import walkforward as wf
from ai_trader.research.lab import markdown, study
from ai_trader.research.strategies import BENCHMARK, CANDIDATES

S = rd.synthetic(1500)


@pytest.mark.parametrize("cand", CANDIDATES, ids=lambda c: c.name)
def test_no_rule_uses_future_bars(cand):
    for params in (cand.grid[0], cand.grid[-1]):
        full = cand.weights(S, params)
        for cut in (400, 777, 1203):
            assert cand.weights(S.prefix(cut), params) == full[:cut]
        assert all(0.0 <= w <= 1.0 for w in full)


def test_weight_is_traded_on_the_next_open_not_the_same_bar():
    w = [0.0] * len(S)
    w[10] = 1.0  # decided on bar 10's close
    w[11] = 1.0
    r = bt.run(S, w, bt.ZERO_COSTS, start=1, end=14)
    assert r.exposure[9] == 0.0  # bar 10: still flat
    assert r.exposure[10] > 0.99  # bar 11: bought at its open
    assert r.equity[10] == pytest.approx(S.close[11] / S.open[11])
    assert r.exposure[12] == 0.0 and len(r.trades) == 1


def test_costs_reduce_equity_by_exactly_fee_and_slippage():
    w = [1.0] * len(S)
    free = bt.run(S, w, bt.ZERO_COSTS, start=1, end=50)
    paid = bt.run(S, w, bt.Costs(fee=0.0025, slippage=0.0005), start=1, end=50)
    side = (1 - 0.0025) * (1 - 0.0005)
    assert paid.equity[-1] / free.equity[-1] == pytest.approx((1 - 0.0025) / 1.0005 * side)
    assert paid.orders == 2 and len(paid.trades) == 1 and paid.open_trade
    assert paid.trades[0] == pytest.approx(paid.equity[-1] - 1)


def test_never_leveraged_and_never_short():
    w = [5.0 if i % 7 < 3 else -2.0 for i in range(len(S))]
    r = bt.run(S, w, start=1)
    assert all(-1e-9 <= e <= 1 + 1e-9 for e in r.exposure)
    assert min(r.equity) > 0


def test_holdout_is_never_read_unless_asked():
    S = rd.synthetic(2600)
    a = wf.evaluate(S, CANDIDATES[0])
    poisoned_rows = list(zip(S.ts, S.open, S.high, S.low, S.close, S.volume))
    holdout, folds = wf.Plan().split(len(S))
    assert folds[-1][1] <= holdout
    crash = [(t, o / 50, h / 50, l / 50, c / 50, v) if i >= holdout + 1 else (t, o, h, l, c, v)
             for i, (t, o, h, l, c, v) in enumerate(poisoned_rows)]
    b = wf.evaluate(rd._from_rows("SYN", "1d", crash), CANDIDATES[0])
    assert a["holdout"] is None and a["oos"] == b["oos"] and a["folds"] == b["folds"]
    assert wf.evaluate(S, CANDIDATES[0], use_holdout=True)["holdout"]["bars"] == len(S) - holdout


def test_validation_refuses_corrupt_history():
    rows = list(zip(S.ts, S.open, S.high, S.low, S.close, S.volume))
    rows[5] = (rows[5][0], 10.0, 9.0, 8.0, 10.0, 1.0)  # high below open
    with pytest.raises(rd.ResearchDataError):
        rd.validate(rd._from_rows("X", "1d", rows))
    dup = rows[:4] + [rows[3]] + rows[6:]
    with pytest.raises(rd.ResearchDataError):
        rd.validate(rd._from_rows("X", "1d", dup))
    assert rd.validate(rd._from_rows("X", "1d", rows[:5] + rows[8:]))["missing_bars"] == 3


def test_fetch_keeps_completed_bars_only_and_pages():
    step = 86400
    pages = [[[i * step * 1000, "1", "2", "0.5", "1.5", "10"] for i in range(1000)],
             [[i * step * 1000, "1", "2", "0.5", "1.5", "10"] for i in range(1000, 1003)]]
    s = rd.fetch("BTCUSDT", "1d", now=1002 * step + 5, get=lambda url: pages.pop(0), pause=0)
    assert len(s) == 1002 and s.ts[-1] == 1001 * step


def _ev(**over):
    oos = dict(trades=40, years=5.0, net_return=0.8, profit_factor=1.6, max_drawdown=0.2, sharpe=1.1)
    ev = dict(oos=oos, oos_stress=dict(net_return=0.5), folds_traded=1.0, folds_positive=0.8, grid_positive=0.9,
              oos_excess=dict(p_le_zero=0.001),
              holdout=dict(trades=8, net_return=0.1, max_drawdown=0.1, sharpe=0.9))
    for k, v in over.items():
        if k in oos:
            oos[k] = v
        else:
            ev[k] = v
    return ev


def test_gate_verdicts():
    ok = q.assess(_ev(), n_candidates=9, deflated_sharpe=0.97)
    assert ok["verdict"] == "PAPER_CANDIDATE" and ok["label"] == "timing_edge"
    beta = q.assess(_ev(oos_excess=dict(p_le_zero=0.3)), n_candidates=9, deflated_sharpe=0.97)
    assert beta["verdict"] == "PAPER_CANDIDATE" and beta["label"] == "risk_managed_beta"
    assert q.assess(_ev(trades=6), n_candidates=9, deflated_sharpe=0.97)["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert q.assess(_ev(trades=6, max_drawdown=0.5), n_candidates=9, deflated_sharpe=0.97)["verdict"] == "REJECTED"
    assert q.assess(_ev(holdout=None), n_candidates=9, deflated_sharpe=0.97)["verdict"] == "INSUFFICIENT_EVIDENCE"
    for bad in (dict(max_drawdown=0.4), dict(net_return=-0.1), dict(oos_stress=dict(net_return=-0.01)),
                dict(holdout=dict(trades=8, net_return=-0.1, max_drawdown=0.1, sharpe=0.9))):
        assert q.assess(_ev(**bad), n_candidates=9, deflated_sharpe=0.97)["verdict"] == "REJECTED"
    assert q.assess(_ev(), n_candidates=9, deflated_sharpe=0.5)["verdict"] == "REJECTED"


def test_no_history_verdict_is_ever_live_eligible():
    assert q.assess(_ev(), n_candidates=1, deflated_sharpe=1.0)["verdict"] != "LIVE_PILOT_ELIGIBLE"
    good = dict(days=90, trades=30, net_return=0.05, max_drawdown=0.05, execution_error_rate=0.0)
    assert q.assess_paper(good) == {"verdict": "LIVE_PILOT_ELIGIBLE", "failed": [], "requires_owner_approval": True}
    assert q.assess_paper({**good, "days": 10})["verdict"] == "PAPER_CANDIDATE"
    assert q.assess_paper({})["verdict"] == "PAPER_CANDIDATE"


def test_metrics_on_a_known_curve():
    r = bt.Result(1, 5, equity=[1.1, 0.99, 1.2, 1.08], exposure=[1, 1, 1, 1], asset_ret=[0.1, -0.1, 0.2, -0.1],
                  trades=[0.1, -0.05, -0.02, 0.2])
    m = mx.summarise(r, 365.25)
    assert m["max_drawdown"] == pytest.approx(0.1) and m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(0.3 / 0.07) and m["max_consecutive_losses"] == 2
    assert m["net_return"] == pytest.approx(0.08)


def test_a_random_walk_shows_no_timing_edge():
    flat = rd.synthetic(3000, seed=3, drift=0.0)
    report = study({"SYN": flat}, use_holdout=True)
    verdicts = {r["candidate"]: r["assessment"] for r in report["results"] if r["assessment"]}
    assert not any(a["label"] == "timing_edge" for a in verdicts.values())
    assert "Not trading profits" in markdown(report)
    assert report["results"][-1]["candidate"] == BENCHMARK.name


def test_deflated_sharpe_penalises_many_trials():
    import random
    rng = random.Random(1)
    rets = [rng.gauss(0.001, 0.02) for _ in range(1000)]
    few = mx.deflated_sharpe(rets, [0.0, 0.01])
    many = mx.deflated_sharpe(rets, [rng.gauss(0, 0.05) for _ in range(200)])
    assert 0 <= many < few <= 1 and not math.isnan(few)
