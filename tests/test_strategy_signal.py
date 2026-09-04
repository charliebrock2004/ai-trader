"""The live candidate detector.

Two kinds of test live here and they answer different questions.

**Mechanism tests** use a constructed price path and ask whether the logic does
what it says: does the regime gate hold, does each rejection fire for its own
reason, does a restart change nothing. A constructed path is the right tool for
that because it isolates one condition at a time.

**Behaviour tests** use real recorded BTC history and ask the one question a
constructed path cannot answer honestly: does this detector actually find
anything in a real market, often enough to be evaluated? That is the question
the old crossover filter failed — it was correct, and it found nothing for days.

Neither kind is evidence of profitability, and nothing here asserts a return.
Whether the strategy makes money is for the recorded paper trades to answer.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ai_trader.paper.models import PaperAction
from ai_trader.strategy.signal import (
    REJECTIONS,
    SignalConfig,
    TrendPullbackStrategy,
    reference_volatility,
)

FIXTURE = Path(__file__).parent / "fixtures" / "btc_daily_closes.json"


def real_btc_closes() -> list[float]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["closes"]


def trending(count: int, *, base: float = 100.0, drift: float = 0.0012) -> list[float]:
    """A deterministic uptrend with repeating pullbacks. Mechanism fixture only."""
    amplitude, period = 0.004, 13
    return [
        base * math.exp(drift * i) * (1.0 + amplitude * math.sin(2 * math.pi * i / period))
        for i in range(count)
    ]


def flat(count: int, *, value: float = 100.0) -> list[float]:
    return [value] * count


# ==========================================================================
# The regression this module exists to prevent
# ==========================================================================
def test_the_detector_finds_opportunities_in_real_market_history() -> None:
    """The whole point. A detector that never fires cannot be evaluated.

    The previous filter required an exact SMA crossover — true on one bar and
    false on every other — so the desk went days without a single candidate.
    This asserts the replacement produces a workable number of candidates on
    genuine BTC history, and deliberately asserts *nothing* about whether they
    were profitable.
    """
    closes = real_btc_closes()
    strategy = TrendPullbackStrategy(timeframe="1d")
    candidates = 0
    evaluated = 0
    for index in range(30, len(closes)):
        evaluated += 1
        if strategy.evaluate(closes[: index + 1]).action == PaperAction.BUY:
            candidates += 1

    assert candidates > 0, "a detector that never fires cannot be evaluated"
    rate = candidates / evaluated
    # Wide bounds on purpose. The lower bound is the silent-desk regression;
    # the upper bound catches a detector that has degenerated into "always buy".
    assert 0.005 < rate < 0.15, f"candidate rate {rate:.3%} is outside a workable range"


def test_real_history_produces_varied_rejection_reasons() -> None:
    """Silence must be explainable, and not by one reason swallowing everything.

    A single reason accounting for nearly every bar is the signature of a
    mis-scaled gate — which is exactly how the first draft of this strategy was
    broken, with a volatility band sized for the wrong timeframe.
    """
    closes = real_btc_closes()
    strategy = TrendPullbackStrategy(timeframe="1d")
    for index in range(30, len(closes)):
        strategy.evaluate(closes[: index + 1])

    counts = strategy.rejection_counts
    assert len(counts) >= 4, f"only {len(counts)} distinct reasons: {counts}"
    total = sum(counts.values())
    dominant = max(counts.values()) / total
    assert dominant < 0.75, f"one reason explains {dominant:.0%} of all holds: {counts}"
    for key in counts:
        assert key in REJECTIONS, f"{key} has no published meaning"


def test_it_is_not_a_crossover_and_can_fire_on_consecutive_bars() -> None:
    """The structural fix, stated as a test.

    A crossover is true on one bar per trend. A regime plus a recurring trigger
    can be true again — which is what makes the desk resilient to restarting,
    sleeping, or refetching its tape.
    """
    closes = trending(120)
    strategy = TrendPullbackStrategy(timeframe="5m")
    hits = [
        index
        for index in range(len(closes))
        if strategy.evaluate(closes[: index + 1]).action == PaperAction.BUY
    ]
    assert len(hits) > 1, "a single firing means this is still an event filter"
    assert any(b - a == 1 for a, b in zip(hits, hits[1:])), "must be able to fire twice running"


# ==========================================================================
# Each gate, in isolation
# ==========================================================================
def test_a_flat_market_is_refused_and_says_why() -> None:
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(flat(60))
    assert signal.action == PaperAction.HOLD
    assert signal.rejection in {"averages_entangled", "too_quiet", "no_uptrend"}
    assert signal.reason


def test_a_downtrend_is_refused_as_no_uptrend() -> None:
    closes = trending(80, drift=-0.0012)
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(closes)
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == "no_uptrend"


def test_a_vertical_spike_is_refused_as_overextended() -> None:
    """Buying a gap is chasing, and the stop would sit an absurd distance away."""
    closes = trending(60) + [200.0]
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(closes)
    assert signal.action == PaperAction.HOLD
    assert signal.rejection in {"overextended", "too_wild"}


def test_warming_up_is_reported_rather_than_guessed() -> None:
    signal = TrendPullbackStrategy(timeframe="5m").evaluate([100.0, 101.0, 102.0])
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == "warming_up"


def test_every_rejection_key_has_a_published_meaning() -> None:
    """The audit trail stores these keys; an unexplained key is a dead end."""
    for key, meaning in REJECTIONS.items():
        assert meaning.strip(), key
        assert meaning.endswith("."), f"{key}: reasons are sentences"


# ==========================================================================
# Position handling
# ==========================================================================
def test_it_does_not_add_to_an_open_position() -> None:
    closes = trending(80)
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(closes, has_position=True)
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == "already_long"


def test_it_exits_when_the_trend_regime_breaks() -> None:
    """Exit is a state test, so a position can always leave a dead trend."""
    closes = trending(60) + [c * 0.97 for c in trending(20, base=100.0 * math.exp(0.0012 * 60))]
    strategy = TrendPullbackStrategy(timeframe="5m")
    exits = [
        index
        for index in range(30, len(closes))
        if strategy.evaluate(closes[: index + 1], has_position=True).action == PaperAction.SELL
    ]
    assert exits, "a held position must be able to leave a broken regime"


# ==========================================================================
# Timeframe scaling — the bug caught before this shipped
# ==========================================================================
def test_volatility_gates_scale_with_the_bar_duration() -> None:
    """Fixed volatility numbers silently become a different strategy per timeframe.

    Hard-coded bounds sized for daily bars reject essentially every 5-minute
    bar as too quiet, which reproduces the silent desk this module replaced.
    """
    five = SignalConfig.for_timeframe("5m")
    daily = SignalConfig.for_timeframe("1d")
    assert five.min_volatility < daily.min_volatility
    assert five.max_volatility < daily.max_volatility
    # Volatility scales with the square root of time: a day is 288 five-minute
    # bars, so daily swings should be roughly sqrt(288) ~ 17x larger.
    ratio = reference_volatility("1d") / reference_volatility("5m")
    assert 15 < ratio < 20


def test_the_horizon_is_long_enough_for_a_move_to_travel_the_stop() -> None:
    """The horizon must give a typical move room to reach the stop.

    Exact equality only holds while the horizon is free. On daily bars it
    clamps at one — a single day already swings further than a 2% stop — so the
    invariant is a floor, not an identity.
    """
    for timeframe in ("5m", "15m", "1h", "1d"):
        config = SignalConfig.for_timeframe(timeframe)
        travelled = reference_volatility(timeframe) * math.sqrt(config.horizon_bars)
        assert travelled >= config.stop_pct * 0.85, timeframe
        if config.horizon_bars > 1:
            assert math.isclose(travelled, config.stop_pct, rel_tol=0.15), timeframe


def test_the_edge_gate_is_measured_against_costs_not_the_stop() -> None:
    """A gate derived from the stop is an equality by construction.

    The horizon is defined as the time a one-sigma move needs to travel the
    stop, so requiring one sigma to travel the stop rejects every below-average
    bar — half the market, for no stated reason. Costs are the real floor.
    """
    config = SignalConfig.for_timeframe("5m")
    required = config.round_trip_cost_pct * config.min_reward_to_cost
    at_reference = reference_volatility("5m") * math.sqrt(config.horizon_bars)
    assert required < at_reference, "a typical bar must be able to clear the edge gate"


def test_costs_are_never_a_reason_to_trade_more() -> None:
    """Raising the cost assumption may only make the desk more selective."""
    base = SignalConfig.for_timeframe("5m")
    pricier = SignalConfig.for_timeframe("5m", round_trip_cost_pct=base.round_trip_cost_pct * 4)
    closes = trending(120)

    def count(config: SignalConfig) -> int:
        strategy = TrendPullbackStrategy(config)
        return sum(
            1
            for index in range(len(closes))
            if strategy.evaluate(closes[: index + 1]).action == PaperAction.BUY
        )

    assert count(pricier) <= count(base)
