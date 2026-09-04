"""Deterministic candidate detection for the live paper desk.

Why this module exists
----------------------

The desk previously used ``SimpleTechnicalSource`` — the frozen SMA 10/20
crossover from the benchmark suite — as its live candidate detector. It found
nothing for days, and the reason is structural rather than a matter of
thresholds: a crossover is a *point event*. It is true on exactly the one bar
where the fast average passes through the slow one, and false on every other
bar. Miss that bar — because the worker restarted, because the host slept,
because the series was refetched — and the entire trend that followed is
invisible. On top of that, the desk only walks the candles it has actually
seen, so an unattended crossover is gone for good.

The fix is to split the question the way a trader would:

* **Regime** is a *state*: is the market in an uptrend right now? True for as
  long as it is true, so it survives a restart.
* **Trigger** is a *recurring* event inside that state: has price pulled back
  toward the mean and started up again? This can happen many times per trend.

That is the whole change. It is not a loosened filter — the conditions below
are, if anything, stricter than a bare crossover, because a crossover asks
nothing about separation, slope, extension, or volatility. It simply asks a
question that can be true more than once.

What this deliberately is not
-----------------------------

This is a plain trend-continuation strategy on a liquid instrument. It is not
expected to have an edge, and nothing here should be read as a claim that it
does. Its job is to produce a stream of *genuine, explainable* candidates so
the recorded evidence can settle the question. Every rejection carries a named
reason so that "no opportunity existed" is distinguishable from "the code threw
an opportunity away", which is the failure mode that produced the silent desk
in the first place.

Close-only, on purpose
----------------------

Every input is a close price. Highs and lows differ between venues and are the
first thing a thin book distorts, so a strategy that depends on them is
measuring the exchange as much as the market. Closes are also what the recorded
history holds, which means this logic can be replayed honestly against it.

The thresholds
--------------

Chosen a priori from economics, not fitted to a return series. Each is a round
number with a reason stated at its definition. They were checked against
recorded BTC history for *how often they fire* — a strategy that fires twice a
year cannot be evaluated in a month — and never against whether firing made
money. Tuning for frequency is sample-size management; tuning for profit on the
same data you then report is how backtests lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.analysis.indicators import rolling_volatility, sma
from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis

# ---------------------------------------------------------------------------
# Rejection vocabulary
#
# Every path that declines to trade names itself here. These strings are
# written to the audit trail and aggregated on the performance page, so that
# "why is it not trading" is answerable from data rather than by reading code.
# ---------------------------------------------------------------------------
REJECTIONS: dict[str, str] = {
    "warming_up": "Not enough candles yet to compute the slow average.",
    "no_uptrend": "Fast average is below the slow average. No long regime.",
    "averages_entangled": "The two averages are too close to call a trend.",
    "trend_not_rising": "The slow average is flat or falling, so the trend is not confirmed.",
    "overextended": "Price is too far above the fast average. Entering here is chasing.",
    "no_pullback": "Price has not pulled back toward the mean recently.",
    "no_resumption": "The pullback has not turned back up yet.",
    "too_quiet": "Realised volatility is too low for the move to clear costs.",
    "too_wild": "Realised volatility is too high for the stop to be meaningful.",
    "move_too_small": "The plausible move over the holding horizon is too small to clear trading costs.",
    "already_long": "Already holding this symbol. Not adding to a position.",
}

#: Emitted when the regime breaks while a position is open.
EXIT_REASON = "Fast average crossed below the slow average. Trend regime over."


#: Bar durations the desk can run on, in seconds.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

#: One-day standard deviation of BTC returns, to one significant figure. This
#: is the single empirical constant in the file and it is a well-known property
#: of the instrument, not something fitted here. Everything volatility-related
#: is derived from it by square-root-of-time scaling, so the strategy behaves
#: the same way on 5-minute bars as on daily ones instead of silently becoming
#: a different strategy when the timeframe changes.
BTC_DAILY_VOLATILITY = 0.035


def reference_volatility(timeframe: str) -> float:
    """Typical one-bar return standard deviation at this timeframe."""
    seconds = TIMEFRAME_SECONDS.get(timeframe, 300)
    return BTC_DAILY_VOLATILITY * (seconds / 86400.0) ** 0.5


@dataclass(frozen=True)
class SignalConfig:
    """Frozen strategy parameters.

    Every value is a round number justified by market economics rather than
    fitted to a price series. Changing one is a strategy change and belongs in
    a commit that says so.

    The volatility band and the holding horizon are **not** fixed constants:
    they are derived from the bar duration by :meth:`for_timeframe`. Hard-coding
    them was a real bug caught before this shipped — a band sized for daily bars
    rejects almost every 5-minute bar as "too quiet", and a 12-bar horizon
    against a 2% stop rejects every 5-minute bar as "unresolvable". Either one
    reproduces exactly the silent desk this module exists to fix.
    """

    #: The two averages. 10/20 is the textbook fast/slow pair; keeping it means
    #: the live desk and the frozen benchmark are looking at the same trend, so
    #: their results are comparable.
    fast: int = 10
    slow: int = 20

    #: Bars used to measure whether the slow average is rising.
    slope_bars: int = 5

    #: Minimum gap between the averages, as a fraction of the slow one. Below
    #: this the two lines are effectively the same line and "which is on top"
    #: is noise, not a trend.
    min_separation: float = 0.0010

    #: Maximum distance above the fast average at entry. Beyond this the move
    #: has already happened and the stop sits an unreasonable distance away.
    max_extension: float = 0.0150

    #: A pullback is price coming back within this fraction of the fast
    #: average. Without one, we would be buying the top of an extended leg.
    pullback_touch: float = 0.0040
    #: How recently that pullback must have happened.
    pullback_lookback: int = 5

    #: Realised volatility band, as one-bar standard deviation of returns, set
    #: as multiples of the timeframe's reference volatility. Below the floor the
    #: instrument is not moving enough to pay the spread; above the ceiling the
    #: stop sits inside the noise and gets hit at random.
    min_volatility: float = 0.25 * reference_volatility("5m")
    max_volatility: float = 4.00 * reference_volatility("5m")

    #: Bars a position is expected to be held: how long a typical move takes to
    #: travel the stop distance at this timeframe. Used only to ask whether the
    #: market can plausibly resolve the trade at all.
    horizon_bars: int = 90
    #: The stop the risk engine will apply. Mirrored here only to size the
    #: horizon; the risk engine remains the sole authority that sets the stop.
    stop_pct: float = 0.0200

    #: Round-trip cost of a paper trade: spread plus slippage, both ways. Must
    #: track the simulator's SPREAD_BPS + SLIP_BPS or this gate lies.
    round_trip_cost_pct: float = 0.0020
    #: The plausible move over the horizon must be at least this multiple of
    #: the round-trip cost. This is the minimum-edge test in spot terms: below
    #: it, the only party reliably paid is the exchange.
    #:
    #: Deliberately *not* expressed against the stop distance. The horizon is
    #: defined as "bars for a one-sigma move to travel the stop", so a gate of
    #: "one sigma must travel the stop" is an equality by construction and
    #: rejects every below-average bar — a coin flip dressed as a filter. It
    #: would also be measuring the wrong thing: this strategy exits when the
    #: trend regime breaks, not only at the stop or the target, so a trade that
    #: never reaches either is not a dead trade. What genuinely kills a trade
    #: is a market too quiet to cover its own costs.
    min_reward_to_cost: float = 5.0

    @classmethod
    def for_timeframe(cls, timeframe: str, **overrides: Any) -> "SignalConfig":
        """Scale the volatility band and horizon to the bar duration.

        Volatility scales with the square root of time, so the band is a fixed
        multiple of what this timeframe normally does, and the horizon is the
        number of bars a one-sigma move needs to travel the stop distance.
        """
        reference = reference_volatility(timeframe)
        stop = float(overrides.get("stop_pct", cls.stop_pct))
        horizon = max(1, round((stop / reference) ** 2))
        derived: dict[str, Any] = {
            "min_volatility": 0.25 * reference,
            "max_volatility": 4.00 * reference,
            "horizon_bars": horizon,
        }
        derived.update(overrides)
        return cls(**derived)


@dataclass
class Signal:
    """One evaluated bar: what was decided, and everything behind it."""

    action: PaperAction
    #: ``None`` when the strategy wants to trade. Otherwise a key of REJECTIONS.
    rejection: Optional[str] = None
    reason: str = ""
    #: Named indicator values, for the audit trail and the analyst payload.
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_candidate(self) -> bool:
        return self.action in (PaperAction.BUY, PaperAction.SELL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "rejection": self.rejection,
            "reason": self.reason,
            "features": dict(self.features),
        }


def _hold(key: str, features: dict[str, Any]) -> Signal:
    return Signal(
        action=PaperAction.HOLD,
        rejection=key,
        reason=REJECTIONS[key],
        features=features,
    )


class TrendPullbackStrategy:
    """Buy a confirmed uptrend after a pullback; leave when the trend ends.

    Long-only, because the paper simulator is long-only. ``SELL`` here means
    "close the position", never "go short".
    """

    name = "trend-pullback-10-20"

    def __init__(
        self,
        config: Optional[SignalConfig] = None,
        *,
        timeframe: str = "5m",
    ) -> None:
        self.config = config or SignalConfig.for_timeframe(timeframe)
        self.timeframe = timeframe
        #: Every rejection, counted. This is the record that answers "why did
        #: it not trade" without anyone having to re-run the strategy.
        self.rejection_counts: dict[str, int] = {}
        self.evaluated = 0
        self.candidates = 0

    # -- the decision ------------------------------------------------------
    def evaluate(
        self,
        closes: list[float],
        *,
        has_position: bool = False,
    ) -> Signal:
        """Evaluate the latest close. ``closes`` ends at the bar being decided."""
        cfg = self.config
        self.evaluated += 1

        features: dict[str, Any] = {"strategy": self.name, "bars": len(closes)}

        if len(closes) < cfg.slow + cfg.slope_bars + 1:
            return self._count(_hold("warming_up", features))

        price = closes[-1]
        fast = sma(closes, cfg.fast)
        slow = sma(closes, cfg.slow)
        slow_then = sma(closes[: -cfg.slope_bars], cfg.slow)
        volatility = rolling_volatility(closes)

        if fast is None or slow is None or slow_then is None or not slow:
            return self._count(_hold("warming_up", features))

        separation = (fast - slow) / slow
        slope = (slow - slow_then) / slow_then if slow_then else 0.0
        extension = (price - fast) / fast if fast else 0.0

        features.update(
            {
                "price": price,
                "sma_fast": fast,
                "sma_slow": slow,
                "separation": separation,
                "slow_slope": slope,
                "extension_above_fast": extension,
                "volatility_per_bar": volatility,
                "regime": "UP" if fast > slow else "DOWN",
            }
        )

        # --- Exit first. A position must be able to leave a broken regime
        # even on a bar where no entry would be considered.
        if has_position:
            if fast < slow:
                self.candidates += 1
                return Signal(
                    action=PaperAction.SELL,
                    reason=EXIT_REASON,
                    features=features,
                )
            return self._count(_hold("already_long", features))

        # --- Regime: a state, not an event.
        if fast <= slow:
            return self._count(_hold("no_uptrend", features))
        if separation < cfg.min_separation:
            return self._count(_hold("averages_entangled", features))
        if slope <= 0:
            return self._count(_hold("trend_not_rising", features))

        # --- Volatility has to be in a band where the risk engine's stop means
        # something. This is a quality gate, not a throttle.
        if volatility is None:
            return self._count(_hold("warming_up", features))
        if volatility < cfg.min_volatility:
            return self._count(_hold("too_quiet", features))
        if volatility > cfg.max_volatility:
            return self._count(_hold("too_wild", features))

        # --- Minimum edge, in spot terms. A one-sigma move over the expected
        # holding period has to be worth several times the cost of getting in
        # and out, or the trade is a coin flip that reliably pays only the
        # spread.
        plausible_move = volatility * (cfg.horizon_bars**0.5)
        required_move = cfg.round_trip_cost_pct * cfg.min_reward_to_cost
        features["plausible_move"] = plausible_move
        features["required_move"] = required_move
        features["round_trip_cost"] = cfg.round_trip_cost_pct
        features["stop_pct"] = cfg.stop_pct
        if plausible_move < required_move:
            return self._count(_hold("move_too_small", features))

        # --- Trigger: pulled back toward the mean, and turning back up.
        if extension > cfg.max_extension:
            return self._count(_hold("overextended", features))

        window = closes[-cfg.pullback_lookback :]
        trough = min(window)
        pullback_depth = (trough - fast) / fast if fast else 0.0
        features["pullback_depth"] = pullback_depth
        if pullback_depth > cfg.pullback_touch:
            return self._count(_hold("no_pullback", features))

        rising = price > closes[-2] and price > fast
        features["resuming"] = rising
        if not rising:
            return self._count(_hold("no_resumption", features))

        self.candidates += 1
        return Signal(
            action=PaperAction.BUY,
            reason=(
                f"Uptrend confirmed (fast {separation:.2%} above slow, slope "
                f"{slope:+.2%}), price pulled back to within {pullback_depth:+.2%} "
                f"of the fast average and is turning up."
            ),
            features=features,
        )

    def _count(self, signal: Signal) -> Signal:
        if signal.rejection:
            self.rejection_counts[signal.rejection] = (
                self.rejection_counts.get(signal.rejection, 0) + 1
            )
        return signal

    def summary(self) -> dict[str, Any]:
        """Evidence about the detector itself, for the dashboard."""
        return {
            "strategy": self.name,
            "bars_evaluated": self.evaluated,
            "candidates": self.candidates,
            "rejections": dict(self.rejection_counts),
            "rejection_meanings": {
                key: REJECTIONS[key] for key in sorted(self.rejection_counts)
            },
        }

    # -- SignalSource adapter ---------------------------------------------
    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        """Adapter for the paper simulator's ``SignalSource`` protocol."""
        return self.last_signal(index, series).action

    def last_signal(self, index: int, series: CandleSeries, *, has_position: bool = False) -> Signal:
        if len(series.candles) != index + 1:
            raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")
        closes = [candle.close for candle in series.candles]
        signal = self.evaluate(closes, has_position=has_position)
        self.latest = signal
        return signal


def evaluate(closes: list[float], *, has_position: bool = False) -> Signal:
    """One-shot evaluation with the frozen defaults."""
    return TrendPullbackStrategy().evaluate(closes, has_position=has_position)
