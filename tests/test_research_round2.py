"""Round 2: brake, basket and 4-hour candidates obey the same rules as round 1."""

import pytest

from ai_trader.research import backtest as bt
from ai_trader.research import data as rd
from ai_trader.research import round2 as r2
from ai_trader.research import strategies as st
from ai_trader.research import walkforward as wf

S = rd.synthetic(1500)
B = r2.Basket.of([rd.synthetic(1500, seed=1, symbol="A"), rd.synthetic(1400, seed=2, symbol="B"),
                  rd.synthetic(1500, seed=3, symbol="C")])


@pytest.mark.parametrize("cand", r2.DAILY + r2.FOUR_HOUR, ids=lambda c: c.name)
def test_no_single_asset_rule_uses_future_bars(cand):
    for params in (cand.grid[0], cand.grid[-1]):
        full = cand.weights(S, params)
        for cut in (400, 777, 1203):
            assert cand.weights(S.prefix(cut), params) == full[:cut]
        assert all(0.0 <= w <= 1.0 for w in full)


@pytest.mark.parametrize("cand", r2.BASKET + (r2.BASKET_BENCHMARK,), ids=lambda c: c.name)
def test_no_basket_rule_uses_future_bars_or_leverage(cand):
    for params in (cand.grid[0], cand.grid[-1]):
        full = cand.weights(B, params)
        for cut in (400, 777, 1203):
            assert cand.weights(B.prefix(cut), params) == full[:cut]
        assert all(min(w) >= 0.0 and sum(w) <= 1.0 + 1e-9 for w in full)


def test_basket_keeps_only_bars_every_member_has():
    assert len(B) == 1400 and all(m.ts == B.ts for m in B.members)


def test_brake_goes_flat_after_the_limit_and_waits_out_the_cooldown():
    rows = [(1_500_000_000 + i * 86400, p, p, p, p, 1.0) for i, p in enumerate([100] * 5 + [80] + [80] * 10)]
    s = rd._from_rows("X", "1d", rows)
    w = r2.dd_brake(s, [1.0] * len(s), limit=0.10, cooldown=4)
    assert w[:5] == [1.0] * 5
    assert w[5:9] == [0.0] * 4  # the 20% fall is seen on bar 5's close
    assert w[9:] == [1.0] * 7  # fresh peak after the cooldown


def test_brake_does_not_count_a_gap_it_was_not_holding_through():
    px = [100, 100, 100, 80, 80, 80, 80]
    s = rd._from_rows("X", "1d", [(1_500_000_000 + i * 86400, p, p, p, p, 1.0) for i, p in enumerate(px)])
    base = [0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]  # decided on bar 2's close, bought at bar 3's open of 80
    assert r2.dd_brake(s, base, limit=0.10, cooldown=3) == base


def test_study_refuses_corrupt_daily_candles():
    good = rd.synthetic(2600)
    bad = rd.Series(good.symbol, good.interval, good.ts, good.open, good.high,
                    tuple(-1.0 if i == 50 else v for i, v in enumerate(good.low)), good.close, good.volume)
    with pytest.raises(rd.ResearchDataError):
        r2.study({("SYN", "1d"): bad})


def test_one_asset_basket_matches_the_single_asset_backtester():
    w = st.ema_cross(S, 10, 30)
    one = r2.Basket.of([S])
    a = bt.run(S, w, start=200, end=900)
    b = bt.run(one, [(x,) for x in w], start=200, end=900)
    assert b.equity == pytest.approx(a.equity)
    assert b.trades == pytest.approx(a.trades)
    assert (b.orders, b.exposure, b.asset_ret) == (a.orders, pytest.approx(a.exposure), pytest.approx(a.asset_ret))
    assert b.fees == pytest.approx(a.fees)


def test_basket_costs_and_cash_limit():
    w = [(0.5, 0.5, 0.5)] * len(B)  # asks for 150%; scaled to 100%, never borrowed
    r = bt.run(B, w, start=1, end=60)
    assert max(r.exposure) <= 1.0 + 1e-9
    assert r.equity[-1] < bt.run(B, w, bt.ZERO_COSTS, start=1, end=60).equity[-1]
    assert len(r.trades) == 3  # each holding closed at the end of the window


def test_round2_study_scores_every_section_without_the_holdout():
    series = {("SYN", "1d"): rd.synthetic(2600), ("SYN2", "1d"): rd.synthetic(2600, seed=9, symbol="SYN2"),
              ("SYN", "4h"): rd.synthetic(11000, vol=0.012, interval="4h"),
              ("SYN2", "4h"): rd.synthetic(11000, seed=9, vol=0.012, symbol="SYN2", interval="4h")}
    rep = r2.study(series)
    assert [s["title"] for s in rep["sections"]] == ["SYN daily", "SYN2 daily", "SYN 4-hour", "SYN2 4-hour",
                                                     "Basket SYN+SYN2 daily"]
    scored = [r for s in rep["sections"] for r in s["results"] if r["assessment"]]
    assert len(scored) == 2 * len(r2.DAILY) + 2 * len(r2.FOUR_HOUR) + len(r2.BASKET)
    assert all(r["holdout"] is None and r["assessment"]["verdict"] != "PAPER_CANDIDATE" for r in scored)
    assert rep["candidates_tried_all_rounds"] == len(st.CANDIDATES) + len(r2.ALL)
    assert "Not trading profits" in r2.markdown(rep)
