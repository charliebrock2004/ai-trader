"""The live candidate detector.

Three kinds of test, answering different questions.

**Mechanism** — on a constructed path, does each gate fire for its own reason?
A constructed path is the right tool because it isolates one condition at a
time.

**Behaviour** — on real recorded BTC history, does this find anything at all,
often enough to be evaluated? That is the question the previous detector failed:
it was correct and it found nothing for days.

**Economics** — is the trade geometry solvable? The version before this one
targeted 2.5 ATR against a 1.5 ATR stop while a round trip cost roughly one
ATR, which needed a 60% win rate merely to break even. That is not a strategy
and no amount of signal quality rescues it, so the arithmetic is pinned here.

Nothing in this file asserts a return. Whether the strategy makes money is for
the recorded paper trades to answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_trader.market_data.generator import LCG
from ai_trader.paper.models import PaperAction
from ai_trader.strategy.signal import (
    REGIME_DOWN,
    REGIME_RANGE,
    REGIME_UP,
    REJECTIONS,
    SETUP_BREAKOUT,
    SignalConfig,
    TrendPullbackStrategy,
    reference_volatility,
)

FIXTURE = Path(__file__).parent / "fixtures" / "btc_daily_closes.json"


def real_btc_closes() -> list[float]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["closes"]


def realistic(
    count: int, *, seed: int = 7, base: float = 60000.0,
    drift: float = 0.00012, vol: float = 0.0022,
) -> list[float]:
    """A deterministic path with BTC-like 5-minute volatility.

    Seeded, so it is reproducible; noisy, so indicators behave as they do in a
    real market. A smooth curve is useless here — it pins RSI at 100 and every
    bar reads as overbought, which says nothing about the logic.
    """
    rng = LCG(seed)
    out = [base]
    for _ in range(count - 1):
        shock = sum(rng.next() for _ in range(6)) - 3.0
        out.append(max(1.0, out[-1] * (1.0 + drift + vol * shock)))
    return out


def bars(closes: list[float], *, width: float = 0.0012) -> tuple[list[float], list[float]]:
    """Highs and lows around each close, so ATR is meaningful."""
    return ([c * (1 + width) for c in closes], [c * (1 - width) for c in closes])


# ==========================================================================
# The regression this module exists to prevent
# ==========================================================================
def test_it_finds_opportunities_in_real_market_history() -> None:
    """A detector that never fires cannot be evaluated.

    The predecessor required an exact SMA crossover — true on one bar per trend
    — so the live desk went days without a single candidate.
    """
    closes = real_btc_closes()
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="1d")
    candidates = 0
    evaluated = 0
    for index in range(60, len(closes)):
        evaluated += 1
        signal = strategy.evaluate(
            closes[: index + 1], highs=highs[: index + 1], lows=lows[: index + 1]
        )
        if signal.action == PaperAction.BUY:
            candidates += 1

    assert candidates > 0, "a detector that never fires cannot be evaluated"
    rate = candidates / evaluated
    assert 0.002 < rate < 0.20, f"candidate rate {rate:.3%} is outside a workable range"


def test_it_recognises_more_than_one_kind_of_market() -> None:
    """Regime is classified, not assumed.

    Every hold the old detector recorded live was the regime gate. Naming the
    regime is what turns "it is quiet" into "the market is in a downtrend and
    this desk cannot go short".
    """
    closes = real_btc_closes()
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="1d")
    for index in range(60, len(closes)):
        strategy.evaluate(closes[: index + 1], highs=highs[: index + 1], lows=lows[: index + 1])

    regimes = strategy.regime_counts
    assert set(regimes) >= {REGIME_UP, REGIME_DOWN, REGIME_RANGE}
    for regime, count in regimes.items():
        assert count > 0, regime
    # No single regime may account for essentially everything, or the
    # classifier is not classifying.
    assert max(regimes.values()) / sum(regimes.values()) < 0.85


def test_several_setups_actually_fire_on_real_history() -> None:
    """One pattern ANDed with a strict regime is silence, not selectivity."""
    closes = real_btc_closes()
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="1d")
    for index in range(60, len(closes)):
        strategy.evaluate(closes[: index + 1], highs=highs[: index + 1], lows=lows[: index + 1])
    assert len(strategy.setup_counts) >= 3, strategy.setup_counts


def test_rejection_reasons_stay_varied() -> None:
    """One reason explaining everything is a mis-set gate, not a quiet market.

    This is how two separate calibration bugs were caught before shipping: a
    volatility band sized for the wrong timeframe, and a reward gate that was an
    equality by construction.
    """
    closes = real_btc_closes()
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="1d")
    for index in range(60, len(closes)):
        strategy.evaluate(closes[: index + 1], highs=highs[: index + 1], lows=lows[: index + 1])

    counts = strategy.rejection_counts
    assert len(counts) >= 3, counts
    assert max(counts.values()) / sum(counts.values()) < 0.80, counts
    for key in counts:
        assert key in REJECTIONS, f"{key} has no published meaning"


# ==========================================================================
# Economics — the arithmetic that has to hold before signal quality matters
# ==========================================================================
def test_the_target_clears_round_trip_costs_by_a_workable_margin() -> None:
    """Break-even win rate must be achievable.

    One ATR on 5-minute BTC is about 0.22% and a round trip costs about 0.20%,
    so costs eat roughly a whole ATR per trade. A 1.5/2.5 geometry nets 0.35%
    against 0.53% of risk and needs a 60% win rate to break even; that is a slow
    leak wearing a strategy's clothes.
    """
    config = SignalConfig.for_timeframe("5m")
    atr_pct = reference_volatility("5m")
    reward = config.target_atr * atr_pct - config.round_trip_cost_pct
    risk = config.stop_atr * atr_pct + config.round_trip_cost_pct
    breakeven = risk / (reward + risk)

    assert reward > 0, "the target must clear costs at all"
    assert reward / risk > 1.2, "reward-to-risk after costs is too thin"
    assert breakeven < 0.46, f"needs a {breakeven:.0%} win rate to break even"


def test_costs_are_never_a_reason_to_trade_more() -> None:
    """A higher cost assumption may only make the desk more selective."""
    closes = realistic(900, seed=11)
    highs, lows = bars(closes)

    def count(config: SignalConfig) -> int:
        strategy = TrendPullbackStrategy(config)
        return sum(
            1
            for i in range(60, len(closes))
            if strategy.evaluate(
                closes[: i + 1], highs=highs[: i + 1], lows=lows[: i + 1]
            ).action == PaperAction.BUY
        )

    base = SignalConfig.for_timeframe("5m")
    pricier = SignalConfig.for_timeframe("5m", round_trip_cost_pct=base.round_trip_cost_pct * 5)
    assert count(pricier) <= count(base)


def test_a_higher_threshold_never_trades_more() -> None:
    """Selectivity lives in one number, and it has to be monotone."""
    closes = realistic(900, seed=23)
    highs, lows = bars(closes)

    def count(threshold: float) -> int:
        strategy = TrendPullbackStrategy(
            SignalConfig.for_timeframe("5m", strong_score=threshold)
        )
        return sum(
            1
            for i in range(60, len(closes))
            if strategy.evaluate(
                closes[: i + 1], highs=highs[: i + 1], lows=lows[: i + 1]
            ).action == PaperAction.BUY
        )

    counts = [count(t) for t in (0.50, 0.60, 0.70, 0.80)]
    assert counts == sorted(counts, reverse=True), counts


# ==========================================================================
# Each gate, in isolation
# ==========================================================================
def test_a_downtrend_is_refused_because_the_desk_cannot_go_short() -> None:
    """Stated as a limitation, not worked around.

    The risk engine refuses shorts outright and that is a safety property: a
    short has unbounded loss and needs borrow accounting this ledger does not
    have. Standing aside is the correct behaviour.
    """
    closes = realistic(400, seed=7, drift=-0.0010)
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="5m")
    signal = strategy.evaluate(closes, highs=highs, lows=lows)
    assert signal.action == PaperAction.HOLD
    assert signal.regime == REGIME_DOWN
    assert signal.rejection == "downtrend"


def test_warming_up_is_reported_rather_than_guessed() -> None:
    signal = TrendPullbackStrategy(timeframe="5m").evaluate([100.0, 101.0, 102.0])
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == "warming_up"


def test_a_violently_volatile_market_is_refused() -> None:
    """A stop set in ATR is a much larger loss when ATR has tripled."""
    closes = realistic(400, seed=11, vol=0.030)
    highs, lows = bars(closes, width=0.02)
    strategy = TrendPullbackStrategy(timeframe="5m")
    for index in range(60, len(closes)):
        strategy.evaluate(closes[: index + 1], highs=highs[: index + 1], lows=lows[: index + 1])
    assert strategy.rejection_counts.get("too_wild", 0) > 0


def test_a_stretched_market_is_not_bought() -> None:
    """A vertical line is not an opportunity."""
    closes = [100.0 * (1.02**i) for i in range(120)]
    highs, lows = bars(closes)
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(closes, highs=highs, lows=lows)
    assert signal.action == PaperAction.HOLD
    assert signal.rejection in {"overbought", "too_wild", "no_setup"}


def test_a_breakout_needs_a_bar_that_actually_expanded() -> None:
    """Drifting a tick over an old high in a quiet market is not a breakout.

    Without this the desk bought every crossing of a prior high, which in a
    sideways market is the definition of a false breakout — and chop was
    exactly where it churned.
    """
    config = SignalConfig.for_timeframe("5m")
    assert config.breakout_expansion_atr >= 1.0

    closes = realistic(600, seed=31)
    highs, lows = bars(closes)
    loose = TrendPullbackStrategy(
        SignalConfig.for_timeframe("5m", breakout_expansion_atr=0.0)
    )
    strict = TrendPullbackStrategy(config)
    for index in range(60, len(closes)):
        window = slice(0, index + 1)
        loose.evaluate(closes[window], highs=highs[window], lows=lows[window])
        strict.evaluate(closes[window], highs=highs[window], lows=lows[window])
    assert strict.setup_counts.get(SETUP_BREAKOUT, 0) <= loose.setup_counts.get(
        SETUP_BREAKOUT, 0
    )


# ==========================================================================
# Position handling
# ==========================================================================
def test_it_does_not_add_to_an_open_position() -> None:
    closes = realistic(400, seed=23)
    highs, lows = bars(closes)
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(
        closes, highs=highs, lows=lows, has_position=True
    )
    assert signal.action == PaperAction.HOLD
    assert signal.rejection == "already_long"


def test_a_held_position_leaves_a_broken_regime() -> None:
    """Exit is a state test, so a position can always get out."""
    closes = realistic(400, seed=7, drift=-0.0010)
    highs, lows = bars(closes)
    signal = TrendPullbackStrategy(timeframe="5m").evaluate(
        closes, highs=highs, lows=lows, has_position=True
    )
    assert signal.action == PaperAction.SELL
    assert signal.regime == REGIME_DOWN


# ==========================================================================
# A candidate carries its whole case
# ==========================================================================
def test_a_candidate_states_its_regime_setup_score_and_levels() -> None:
    """The analyst and the audit trail both need the reasoning, not a verdict."""
    closes = realistic(1200, seed=11)
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="5m")
    found = None
    for index in range(60, len(closes)):
        window = slice(0, index + 1)
        signal = strategy.evaluate(closes[window], highs=highs[window], lows=lows[window])
        if signal.action == PaperAction.BUY:
            found = signal
            break

    assert found is not None, "fixture must contain at least one candidate"
    assert found.regime in {REGIME_UP, REGIME_RANGE}
    assert found.setup
    assert found.score >= strategy.config.strong_score
    assert found.entry and found.stop and found.target
    assert found.stop < found.entry < found.target, "levels must bracket the entry"
    assert set(found.components) == {
        "trend", "momentum", "entry_location", "volatility", "structure"
    }
    assert all(0.0 <= v <= 1.0 for v in found.components.values())
    assert found.reason


def test_the_summary_carries_what_the_dashboard_needs() -> None:
    closes = realistic(900, seed=42)
    highs, lows = bars(closes)
    strategy = TrendPullbackStrategy(timeframe="5m")
    for index in range(60, len(closes)):
        window = slice(0, index + 1)
        strategy.evaluate(closes[window], highs=highs[window], lows=lows[window])

    summary = strategy.summary()
    for key in (
        "strategy", "bars_evaluated", "candidates", "threshold",
        "regimes", "setups_seen", "rejections", "rejection_meanings",
        "score_median", "latest",
    ):
        assert key in summary, key
    assert summary["bars_evaluated"] > 0
