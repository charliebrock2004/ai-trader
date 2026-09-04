"""Deterministic opportunity detection for the live paper desk.

What this replaced, and why
---------------------------

The desk had exactly one way to produce a candidate: a pullback inside an
established SMA 10/20 uptrend, with the pullback and its resumption landing on
the same bar. Two things followed from that.

First, the regime gate alone disqualified most of the market. Every hold the
live worker recorded was ``no_uptrend`` or ``averages_entangled`` — it never
reached the entry logic at all. BTC trends up perhaps a third of the time, so
two thirds of the day was unreachable by construction.

Second, a single pattern inside that regime is rare even when the regime holds.
One narrow setup ANDed with a strict regime is not selectivity, it is silence.

The fix is to ask several questions instead of one, and to *score* the answer
rather than take the first pattern that matches:

* **Regime** is classified explicitly — TREND_UP, TREND_DOWN, RANGE — and named
  on the dashboard, so "why is it quiet" is answered before anyone has to ask.
* **Setups** are the ways a long entry can legitimately arise: a pullback in a
  trend, a breakout from a base, momentum continuation, and a bounce from the
  low of an established range. Each is a genuine, named pattern, not a loosened
  version of the last one.
* **Score** is a weighted blend of independent quality components. A setup that
  merely matches is not enough; it has to be a *good* instance of itself.

Selectivity now lives in the score threshold, where it can be reasoned about and
measured, instead of in an accidental conjunction of filters nobody had counted.

Long only, and why
------------------

The paper engine is long-only and the risk engine refuses shorts outright
("Shorts are disabled"). That is a deliberate safety property — a short has
unbounded loss and needs borrow and margin accounting that this ledger does not
have — so this module never proposes one. In a downtrend it reports TREND_DOWN
and stands aside. That is a real limitation, stated rather than worked around,
and it is not something to fix by weakening the risk engine.

What is claimed
---------------

Nothing about profitability. These are standard intraday patterns on a liquid
instrument. The point is a stream of genuine, explainable candidates with the
reasoning recorded, so the paper record can settle whether there is an edge.
Every hold names itself; a single reason dominating the counts is the signature
of a mis-set gate, not of a quiet market.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ai_trader.analysis.indicators import (
    atr,
    ema,
    highest,
    lowest,
    position_in_range,
    rsi,
    sma,
)
from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
#: Market regimes. Named so the dashboard can say what kind of market this is
#: before explaining what the desk did about it.
REGIME_UP = "TREND_UP"
REGIME_DOWN = "TREND_DOWN"
REGIME_RANGE = "RANGE"
REGIME_UNKNOWN = "UNKNOWN"

#: The ways a long entry can legitimately arise.
SETUP_PULLBACK = "PULLBACK_CONTINUATION"
SETUP_BREAKOUT = "BREAKOUT"
SETUP_MOMENTUM = "MOMENTUM_CONTINUATION"
SETUP_RANGE_BOUNCE = "RANGE_BOUNCE"

#: Every reason the desk declines. These are stored in ``decisions.rejection``
#: and counted on the dashboard, so silence is a query rather than a code read.
REJECTIONS: dict[str, str] = {
    "warming_up": "Not enough candles yet to compute the indicators.",
    "downtrend": "Market is in a downtrend and this desk cannot go short.",
    "no_setup": "No recognised setup is present on this candle.",
    "score_too_low": "A setup is present but its quality score is below the threshold.",
    "too_quiet": "Realised volatility is too low for a move to clear costs.",
    "too_wild": "Realised volatility is too high for a stop to be meaningful.",
    "overbought": "Momentum is stretched; entering here is buying the top.",
    "poor_reward": "Reward-to-risk after costs is not worth the trade.",
    "already_long": "Already holding this symbol. Not adding to a position.",
    "no_signal": "No trade signal.",
}

EXIT_REASON = "Trend regime has broken. Closing the long."


@dataclass(frozen=True)
class SignalConfig:
    """Frozen strategy parameters.

    Round numbers chosen from market economics, not fitted to a return series.
    Volatility bounds scale with the bar duration rather than being hard-coded:
    a band sized for daily bars rejects nearly every 5-minute bar as "too
    quiet", which is how the previous version of this file went silent.
    """

    # -- structure ---------------------------------------------------------
    ema_fast: int = 20
    ema_slow: int = 50
    atr_window: int = 14
    rsi_window: int = 14
    #: Lookback for breakout highs and for the range a bounce is measured in.
    structure_window: int = 20

    # -- regime ------------------------------------------------------------
    #: Separation between the averages, measured in ATR, that distinguishes a
    #: trend from two lines wandering around each other. Measuring it in ATR
    #: rather than percent keeps the same meaning in calm and violent markets.
    trend_atr_separation: float = 0.5

    # -- entry quality -----------------------------------------------------
    #: Maximum distance above the fast average at entry, in ATR. Beyond this the
    #: move has already happened and the stop sits an unreasonable way off.
    max_extension_atr: float = 1.5
    #: A pullback is price coming back within this many ATR of the fast average.
    pullback_atr: float = 0.6
    #: RSI above this is stretched. Not a short signal — a reason not to buy.
    overbought_rsi: float = 78.0
    #: A range bounce needs price in the bottom of the range and RSI recovering.
    bounce_range_position: float = 0.30
    bounce_rsi_floor: float = 25.0
    #: A breakout must arrive with a bar bigger than normal. Without this test
    #: the desk buys every drift across a prior high, which in a sideways market
    #: is the definition of a false breakout — and chop was where it churned.
    breakout_expansion_atr: float = 1.2

    # -- volatility band ---------------------------------------------------
    #: Multiples of the timeframe's reference volatility.
    #:
    #: The ceiling was 5x and that was too generous: at 3x normal volatility the
    #: desk traded twice as often and drew down 9%, because a stop set at 2 ATR
    #: is a far larger absolute loss when ATR has tripled, and violent markets
    #: whipsaw through stops on both sides. A strategy should not become *more*
    #: active precisely when its stop means least.
    min_volatility: float = 0.20 * 0.00206
    max_volatility: float = 2.50 * 0.00206

    # -- risk geometry -----------------------------------------------------
    #: Stop distance in ATR. Wide enough to sit outside normal noise, tight
    #: enough that the trade resolves within hours on 5-minute bars — which is
    #: what lets the desk finish and look for the next setup.
    stop_atr: float = 2.0
    #: Target distance in ATR.
    #:
    #: Chosen from cost arithmetic, not fitted to returns. A round trip costs
    #: about 0.20% (spread and slippage, both ways) and one ATR on 5-minute BTC
    #: is about 0.22%, so costs eat roughly a whole ATR per trade. The previous
    #: 1.5/2.5 geometry therefore netted 0.35% against 0.53% of risk — a 0.66
    #: reward-to-risk that needed a 60% win rate merely to break even, which is
    #: not a strategy, it is a slow leak. 2.0/5.0 nets 1.10% against 0.64% and
    #: breaks even at 41.6%.
    target_atr: float = 5.0
    #: Round-trip cost of a paper trade: spread plus slippage, both ways.
    #: Must track the simulator's SPREAD_BPS + SLIP_BPS or this gate lies.
    round_trip_cost_pct: float = 0.0020
    #: Reward after costs must be at least this multiple of the cost itself.
    min_reward_to_cost: float = 4.0

    #: Bars after which an unresolved position is closed at the market.
    #:
    #: A 2.5R target is a long way off, so a trade that does not work quickly
    #: tends to drift sideways and then bleed to the stop, holding the only
    #: position slot the desk has the whole time. Cutting it frees capital for
    #: the next setup, which is the difference between a desk that trades and a
    #: desk that owns one thing all day. 48 five-minute bars is four hours —
    #: roughly twice the time a working trade needs to reach its target.
    max_holding_bars: int = 48

    # -- selectivity -------------------------------------------------------
    #: Score at or above which a setup becomes a candidate and reaches Grok.
    #: This is where selectivity lives. It is one number, it is measurable, and
    #: it can be tuned against recorded outcomes instead of by adding filters.
    strong_score: float = 0.68

    @classmethod
    def for_timeframe(cls, timeframe: str, **overrides: Any) -> "SignalConfig":
        """Scale the volatility band to the bar duration."""
        reference = reference_volatility(timeframe)
        derived: dict[str, Any] = {
            "min_volatility": 0.20 * reference,
            "max_volatility": 2.50 * reference,
        }
        derived.update(overrides)
        return cls(**derived)


#: Bar durations the desk can run on, in seconds.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

#: One-day standard deviation of BTC returns, to one significant figure. The
#: single empirical constant here, and a well-known property of the instrument
#: rather than something fitted. Everything volatility-related scales from it by
#: square-root-of-time, so the strategy means the same thing on any timeframe.
BTC_DAILY_VOLATILITY = 0.035


def reference_volatility(timeframe: str) -> float:
    """Typical one-bar return standard deviation at this timeframe."""
    seconds = TIMEFRAME_SECONDS.get(timeframe, 300)
    return BTC_DAILY_VOLATILITY * (seconds / 86400.0) ** 0.5


@dataclass
class Signal:
    """One evaluated bar: the decision, and everything behind it."""

    action: PaperAction
    regime: str = REGIME_UNKNOWN
    setup: Optional[str] = None
    score: float = 0.0
    #: Named score components, each 0.0-1.0. Shown on the dashboard so a
    #: borderline candidate can be understood without re-running anything.
    components: dict[str, float] = field(default_factory=dict)
    rejection: Optional[str] = None
    reason: str = ""
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_candidate(self) -> bool:
        return self.action in (PaperAction.BUY, PaperAction.SELL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "regime": self.regime,
            "setup": self.setup,
            "score": round(self.score, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "rejection": self.rejection,
            "reason": self.reason,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "features": dict(self.features),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class TrendPullbackStrategy:
    """Score several long setups; take the best one if it is good enough.

    The name is kept because the pullback remains the core idea, but it is no
    longer the only way in. Long-only: ``SELL`` means "close the position",
    never "go short".
    """

    name = "multi-setup-v2"

    def __init__(
        self,
        config: Optional[SignalConfig] = None,
        *,
        timeframe: str = "5m",
    ) -> None:
        self.config = config or SignalConfig.for_timeframe(timeframe)
        self.timeframe = timeframe
        self.rejection_counts: dict[str, int] = {}
        self.regime_counts: dict[str, int] = {}
        self.setup_counts: dict[str, int] = {}
        self.evaluated = 0
        self.candidates = 0
        #: Every score seen, so the threshold can be judged against reality
        #: rather than defended in the abstract.
        self.scores: list[float] = []
        self.latest: Optional[Signal] = None

    # -- indicators --------------------------------------------------------
    def _measure(
        self,
        closes: list[float],
        highs: list[float],
        lows: list[float],
    ) -> Optional[dict[str, Any]]:
        cfg = self.config
        if len(closes) < cfg.ema_slow + 5:
            return None
        fast = ema(closes, cfg.ema_fast)
        slow = ema(closes, cfg.ema_slow)
        slow_then = ema(closes[:-5], cfg.ema_slow)
        band = atr(highs, lows, closes, cfg.atr_window)
        strength = rsi(closes, cfg.rsi_window)
        if None in (fast, slow, slow_then, band, strength) or not band:
            return None
        price = closes[-1]
        # Volatility as ATR relative to price, not the standard deviation of
        # returns. Return-stdev calls a market that rises a steady 2% a bar
        # "perfectly quiet" because the variance of a constant is zero, which
        # is exactly backwards. ATR also keeps this measure consistent with the
        # stops and targets, which are set in ATR.
        volatility = band / price if price else 0.0
        window = cfg.structure_window
        return {
            "price": price,
            "ema_fast": fast,
            "ema_slow": slow,
            "ema_slow_prev": slow_then,
            "atr": band,
            "rsi": strength,
            "volatility": volatility,
            "separation_atr": (fast - slow) / band,
            "slow_slope": (slow - slow_then) / slow_then if slow_then else 0.0,
            "extension_atr": (price - fast) / band,
            "recent_high": highest(closes[:-1], window),
            "recent_low": lowest(closes, window),
            "range_position": position_in_range(
                price, lowest(closes, window), highest(closes, window)
            ),
            "sma_fast": sma(closes, 10),
            "sma_slow": sma(closes, 20),
            "last_bar_range": (highs[-1] - lows[-1]) if highs and lows else 0.0,
        }

    # -- regime ------------------------------------------------------------
    def classify_regime(self, m: dict[str, Any]) -> str:
        cfg = self.config
        separation = m["separation_atr"]
        rising = m["slow_slope"] > 0
        if separation >= cfg.trend_atr_separation and rising:
            return REGIME_UP
        if separation <= -cfg.trend_atr_separation and not rising:
            return REGIME_DOWN
        return REGIME_RANGE

    # -- setups ------------------------------------------------------------
    def _setups(self, m: dict[str, Any], regime: str, closes: list[float]) -> list[str]:
        """Every named pattern present on this bar. May be empty, may be several."""
        cfg = self.config
        found: list[str] = []
        price = m["price"]
        band = m["atr"]
        turning_up = len(closes) >= 2 and price > closes[-2]

        if regime == REGIME_UP:
            # Pullback: price came back toward the fast average and turned up.
            trough = min(closes[-5:])
            if (trough - m["ema_fast"]) / band <= cfg.pullback_atr and turning_up:
                found.append(SETUP_PULLBACK)
            # Momentum: two consecutive higher closes, holding above the mean.
            if (
                len(closes) >= 3
                and closes[-1] > closes[-2] > closes[-3]
                and price > m["ema_fast"]
            ):
                found.append(SETUP_MOMENTUM)

        if regime in (REGIME_UP, REGIME_RANGE):
            # Breakout: a new high for the structure window, on a rising close,
            # and on a bar that actually expanded. Drifting a tick over an old
            # high in a quiet market is not a breakout.
            high = m["recent_high"]
            expanded = m["last_bar_range"] >= cfg.breakout_expansion_atr * band
            if high is not None and price > high and turning_up and expanded:
                found.append(SETUP_BREAKOUT)

        if regime == REGIME_RANGE:
            # Bounce: near the floor of an established range, turning up, and
            # not in freefall.
            position = m["range_position"]
            if (
                position is not None
                and position <= cfg.bounce_range_position
                and turning_up
                and m["rsi"] >= cfg.bounce_rsi_floor
            ):
                found.append(SETUP_RANGE_BOUNCE)

        return found

    # -- score -------------------------------------------------------------
    def _score(self, m: dict[str, Any], regime: str, setup: str) -> dict[str, float]:
        """Independent quality components, each 0.0-1.0.

        Deliberately independent: a setup that scores well on one axis and badly
        on another should land in the middle, not pass because one number was
        excellent.
        """
        cfg = self.config
        components: dict[str, float] = {}

        # Trend alignment. A trend setup wants separation; a range setup does
        # not, so it is scored on the range being well-formed instead.
        if regime == REGIME_UP:
            components["trend"] = _clamp01(m["separation_atr"] / 2.0)
        elif regime == REGIME_RANGE:
            components["trend"] = _clamp01(1.0 - abs(m["separation_atr"]))
        else:
            components["trend"] = 0.0

        # Momentum quality. Best in the upper-middle of RSI: strong enough to
        # continue, not so strong that the move is finished.
        strength = m["rsi"]
        if setup == SETUP_RANGE_BOUNCE:
            # A bounce wants RSI recovering from low, not high.
            components["momentum"] = _clamp01((50.0 - abs(strength - 40.0)) / 50.0)
        else:
            components["momentum"] = _clamp01((strength - 45.0) / 25.0) * _clamp01(
                (cfg.overbought_rsi - strength) / 15.0
            )

        # Not extended. Buying far above the mean is chasing.
        components["entry_location"] = _clamp01(
            1.0 - (m["extension_atr"] / cfg.max_extension_atr)
        )

        # Volatility fitness: best in the middle of the band.
        volatility = m["volatility"]
        span = cfg.max_volatility - cfg.min_volatility
        if span > 0:
            centred = (volatility - cfg.min_volatility) / span
            components["volatility"] = _clamp01(1.0 - abs(centred - 0.45) * 2.0)
        else:
            components["volatility"] = 0.0

        # Room to run: distance to the recent high, in ATR. A breakout has by
        # definition cleared it, so it scores full marks on structure.
        high = m["recent_high"]
        if setup == SETUP_BREAKOUT or high is None:
            components["structure"] = 1.0
        else:
            components["structure"] = _clamp01((high - m["price"]) / (m["atr"] * 2.0))

        return components

    @staticmethod
    def _weights(setup: str) -> dict[str, float]:
        """What matters most differs by setup, so the weights do too."""
        if setup == SETUP_BREAKOUT:
            return {"trend": 0.25, "momentum": 0.25, "entry_location": 0.15,
                    "volatility": 0.25, "structure": 0.10}
        if setup == SETUP_RANGE_BOUNCE:
            return {"trend": 0.15, "momentum": 0.30, "entry_location": 0.20,
                    "volatility": 0.20, "structure": 0.15}
        if setup == SETUP_MOMENTUM:
            return {"trend": 0.30, "momentum": 0.30, "entry_location": 0.20,
                    "volatility": 0.15, "structure": 0.05}
        return {"trend": 0.30, "momentum": 0.20, "entry_location": 0.25,
                "volatility": 0.15, "structure": 0.10}

    # -- the decision ------------------------------------------------------
    def evaluate(
        self,
        closes: list[float],
        *,
        highs: Optional[list[float]] = None,
        lows: Optional[list[float]] = None,
        has_position: bool = False,
    ) -> Signal:
        """Evaluate the latest bar. ``closes`` ends at the bar being decided."""
        cfg = self.config
        self.evaluated += 1
        highs = highs or list(closes)
        lows = lows or list(closes)

        m = self._measure(closes, highs, lows)
        if m is None:
            return self._finish(
                Signal(action=PaperAction.HOLD, rejection="warming_up",
                       reason=REJECTIONS["warming_up"])
            )

        regime = self.classify_regime(m)
        self.regime_counts[regime] = self.regime_counts.get(regime, 0) + 1
        features = {
            "strategy": self.name,
            "price": m["price"],
            "ema_fast": m["ema_fast"],
            "ema_slow": m["ema_slow"],
            "atr": m["atr"],
            "rsi": m["rsi"],
            "volatility_per_bar": m["volatility"],
            "separation_atr": m["separation_atr"],
            "extension_atr": m["extension_atr"],
            "range_position": m["range_position"],
            "sma_fast": m["sma_fast"],
            "sma_slow": m["sma_slow"],
        }

        # Exit first, so a held position can always leave a broken regime even
        # on a bar where no entry would be considered.
        if has_position:
            if regime == REGIME_DOWN:
                self.candidates += 1
                return self._finish(
                    Signal(action=PaperAction.SELL, regime=regime, reason=EXIT_REASON,
                           features=features)
                )
            return self._finish(
                Signal(action=PaperAction.HOLD, regime=regime, rejection="already_long",
                       reason=REJECTIONS["already_long"], features=features)
            )

        def hold(key: str) -> Signal:
            return self._finish(
                Signal(action=PaperAction.HOLD, regime=regime, rejection=key,
                       reason=REJECTIONS[key], features=features)
            )

        # Volatility has to be in a band where a stop means something. This is a
        # quality gate, not a throttle.
        if m["volatility"] < cfg.min_volatility:
            return hold("too_quiet")
        if m["volatility"] > cfg.max_volatility:
            return hold("too_wild")
        if regime == REGIME_DOWN:
            return hold("downtrend")
        if m["rsi"] > cfg.overbought_rsi:
            return hold("overbought")

        setups = self._setups(m, regime, closes)
        if not setups:
            return hold("no_setup")

        # Score every setup present and take the best. A bar can be both a
        # breakout and a momentum continuation; the desk should act on whichever
        # is the stronger reading, not on whichever was checked first.
        best_setup, best_score, best_components = "", -1.0, {}
        for setup in setups:
            components = self._score(m, regime, setup)
            weights = self._weights(setup)
            score = sum(components[k] * weights.get(k, 0.0) for k in components)
            if score > best_score:
                best_setup, best_score, best_components = setup, score, components
        self.scores.append(best_score)
        features["setups_present"] = ",".join(setups)

        # Risk geometry from ATR, so the trade is sized to this market rather
        # than to a fixed percentage that is too tight some days and too wide
        # on others.
        price = m["price"]
        stop = price - cfg.stop_atr * m["atr"]
        target = price + cfg.target_atr * m["atr"]
        reward_pct = (target - price) / price if price else 0.0
        features["reward_pct"] = reward_pct
        features["stop_pct"] = (price - stop) / price if price else 0.0

        if reward_pct < cfg.round_trip_cost_pct * cfg.min_reward_to_cost:
            self.setup_counts[best_setup] = self.setup_counts.get(best_setup, 0) + 1
            return self._finish(
                Signal(action=PaperAction.HOLD, regime=regime, setup=best_setup,
                       score=best_score, components=best_components,
                       rejection="poor_reward", reason=REJECTIONS["poor_reward"],
                       features=features)
            )

        if best_score < cfg.strong_score:
            self.setup_counts[best_setup] = self.setup_counts.get(best_setup, 0) + 1
            return self._finish(
                Signal(
                    action=PaperAction.HOLD, regime=regime, setup=best_setup,
                    score=best_score, components=best_components,
                    rejection="score_too_low",
                    reason=(
                        f"{best_setup} scored {best_score:.2f}, below the "
                        f"{cfg.strong_score:.2f} required to be a candidate."
                    ),
                    features=features,
                )
            )

        self.candidates += 1
        self.setup_counts[best_setup] = self.setup_counts.get(best_setup, 0) + 1
        return self._finish(
            Signal(
                action=PaperAction.BUY, regime=regime, setup=best_setup,
                score=best_score, components=best_components,
                entry=price, stop=stop, target=target,
                reason=(
                    f"{best_setup} in {regime}, quality {best_score:.2f}. "
                    f"RSI {m['rsi']:.0f}, {m['extension_atr']:+.1f} ATR from the "
                    f"fast average. Stop {cfg.stop_atr:.1f} ATR, target "
                    f"{cfg.target_atr:.1f} ATR."
                ),
                features=features,
            )
        )

    def _finish(self, signal: Signal) -> Signal:
        if signal.rejection:
            self.rejection_counts[signal.rejection] = (
                self.rejection_counts.get(signal.rejection, 0) + 1
            )
        self.latest = signal
        return signal

    def summary(self) -> dict[str, Any]:
        """Evidence about the detector itself, for the dashboard."""
        scores = sorted(self.scores)
        return {
            "strategy": self.name,
            "timeframe": self.timeframe,
            "bars_evaluated": self.evaluated,
            "candidates": self.candidates,
            "threshold": self.config.strong_score,
            "regimes": dict(self.regime_counts),
            "setups_seen": dict(self.setup_counts),
            "rejections": dict(self.rejection_counts),
            "rejection_meanings": {
                key: REJECTIONS[key] for key in sorted(self.rejection_counts)
            },
            "score_median": round(scores[len(scores) // 2], 4) if scores else None,
            "score_best": round(scores[-1], 4) if scores else None,
            "latest": self.latest.to_dict() if self.latest else None,
        }

    # -- SignalSource adapter ---------------------------------------------
    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        return self.last_signal(index, series).action

    def last_signal(
        self, index: int, series: CandleSeries, *, has_position: bool = False
    ) -> Signal:
        if len(series.candles) != index + 1:
            raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")
        return self.evaluate(
            [c.close for c in series.candles],
            highs=[c.high for c in series.candles],
            lows=[c.low for c in series.candles],
            has_position=has_position,
        )


def evaluate(
    closes: list[float],
    *,
    highs: Optional[Sequence[float]] = None,
    lows: Optional[Sequence[float]] = None,
    has_position: bool = False,
) -> Signal:
    """One-shot evaluation with the frozen defaults."""
    return TrendPullbackStrategy().evaluate(
        list(closes),
        highs=list(highs) if highs else None,
        lows=list(lows) if lows else None,
        has_position=has_position,
    )
