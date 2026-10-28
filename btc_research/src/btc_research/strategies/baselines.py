"""Pre-registered baseline strategies.

Parameters below were chosen before looking at the sample used by this run.
They are simple and deliberately few. They are not claimed to be optimal.
"""

from __future__ import annotations

import math
from bisect import bisect_left, insort
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from btc_research.data.candles import Candle
from btc_research.strategies.base import assert_exposures

# Non-overlapping UTC sessions. Not optimised on this sample.
SESSION_HOURS = {
    "asia": frozenset(range(0, 7)),
    "europe": frozenset(range(7, 13)),
    "us": frozenset(range(13, 21)),
}


def _hour(open_time_ms: int) -> int:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).hour


def _contiguous(candles: Sequence[Candle], start: int, end: int, interval_ms: int) -> bool:
    """True when candles[start] .. candles[end] inclusive have no gaps."""
    if start < 0 or end >= len(candles) or end < start:
        return False
    expected = (end - start) * interval_ms
    return candles[end].open_time_ms - candles[start].open_time_ms == expected


@dataclass(frozen=True)
class BuyAndHold:
    name: str = "buy_and_hold"

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        return [1.0] * len(candles)


@dataclass(frozen=True)
class Momentum:
    """Long when the trailing close-to-close return is positive, else flat."""

    lookback: int
    interval_ms: int
    name: str = ""

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be positive")
        if not self.name:
            object.__setattr__(self, "name", f"momentum_{self.lookback}")

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        n = len(candles)
        out = [0.0] * n
        lookback = self.lookback
        for t in range(lookback + 1, n):
            end = t - 1
            start = end - lookback
            if not _contiguous(candles, start, end, self.interval_ms):
                continue
            base = candles[start].close
            if base <= 0:
                continue
            out[t] = 1.0 if candles[end].close > base else 0.0
        return assert_exposures(self.name, out, n)


@dataclass(frozen=True)
class MeanReversion:
    """Long after a completed-bar z-score falls below -entry_z; flat at the mean.

    z-score uses the last `window` closes strictly before the decision bar.
    """

    window: int
    interval_ms: int
    entry_z: float = 1.5
    exit_z: float = 0.0
    name: str = ""

    def __post_init__(self) -> None:
        if self.window < 5:
            raise ValueError("window must be at least 5")
        if self.entry_z <= self.exit_z:
            raise ValueError("entry_z must be further from zero than exit_z")
        if not self.name:
            object.__setattr__(self, "name", f"mean_reversion_{self.window}_z{self.entry_z:g}")

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        n = len(candles)
        out = [0.0] * n
        window = self.window
        position = 0.0
        for t in range(n):
            if t < window or not _contiguous(candles, t - window, t - 1, self.interval_ms):
                position = 0.0
                out[t] = 0.0
                continue
            closes = [candles[index].close for index in range(t - window, t)]
            mu = math.fsum(closes) / window
            var = math.fsum((close - mu) ** 2 for close in closes) / (window - 1)
            sigma = math.sqrt(var)
            z_score = 0.0 if sigma == 0 else (closes[-1] - mu) / sigma
            if position == 0.0 and z_score <= -self.entry_z:
                position = 1.0
            elif position == 1.0 and z_score >= -self.exit_z:
                position = 0.0
            out[t] = position
        return assert_exposures(self.name, out, n)


@dataclass(frozen=True)
class VolFilteredMomentum:
    """Momentum, but only while trailing realised vol is at or below its own trailing median.

    Vol and the median are computed from completed bars only.
    """

    mom_lookback: int
    vol_lookback: int
    median_window: int
    interval_ms: int
    name: str = "vol_filtered_momentum"

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        n = len(candles)
        out = [0.0] * n
        window: list[float] = []
        order: list[float] = []
        width = self.median_window
        for t in range(n):
            vol = _realised_vol(candles, t, self.vol_lookback, self.interval_ms)
            median: float | None = None
            if len(window) >= width:
                mid = len(window) // 2
                if len(window) % 2:
                    median = window[mid]
                else:
                    median = 0.5 * (window[mid - 1] + window[mid])
            if (
                vol is not None
                and median is not None
                and t >= self.mom_lookback + 1
                and _contiguous(candles, t - 1 - self.mom_lookback, t - 1, self.interval_ms)
            ):
                base = candles[t - 1 - self.mom_lookback].close
                if base > 0 and candles[t - 1].close > base and vol <= median:
                    out[t] = 1.0
            if vol is not None:
                insort(window, vol)
                order.append(vol)
                if len(order) > width:
                    oldest = order.pop(0)
                    del window[bisect_left(window, oldest)]
        return assert_exposures(self.name, out, n)


def _realised_vol(candles: Sequence[Candle], t: int, lookback: int, interval_ms: int) -> float | None:
    """Stdev of the `lookback` close-to-close returns ending at bar t-1."""
    end = t - 1
    start = end - lookback
    if start < 0 or end >= len(candles):
        return None
    if not _contiguous(candles, start, end, interval_ms):
        return None
    returns: list[float] = []
    for index in range(start + 1, end + 1):
        previous = candles[index - 1].close
        if previous <= 0:
            return None
        returns.append(candles[index].close / previous - 1.0)
    if len(returns) < 2:
        return None
    mu = math.fsum(returns) / len(returns)
    var = math.fsum((value - mu) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(var)


@dataclass(frozen=True)
class TimeWindow:
    """Long only during the given UTC hours of the bar being traded.

    The hour is the bar's open timestamp, which is known when the order is sent.
    """

    hours: frozenset[int]
    name: str

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        out = [1.0 if _hour(candle.open_time_ms) in self.hours else 0.0 for candle in candles]
        return assert_exposures(self.name, out, len(candles))


def default_strategies(interval_ms: int) -> list[object]:
    """The horse-race family. Buy-and-hold is the benchmark, not a candidate edge."""
    return [
        BuyAndHold(),
        Momentum(24, interval_ms),
        Momentum(72, interval_ms),
        Momentum(168, interval_ms),
        MeanReversion(48, interval_ms, entry_z=1.5, exit_z=0.0),
        VolFilteredMomentum(
            mom_lookback=72,
            vol_lookback=72,
            median_window=24 * 30,
            interval_ms=interval_ms,
        ),
        TimeWindow(SESSION_HOURS["asia"], "session_asia"),
        TimeWindow(SESSION_HOURS["europe"], "session_europe"),
        TimeWindow(SESSION_HOURS["us"], "session_us"),
    ]


CANDIDATE_NAMES = (
    "momentum_24",
    "momentum_72",
    "momentum_168",
    "mean_reversion_48_z1.5",
    "vol_filtered_momentum",
    "session_asia",
    "session_europe",
    "session_us",
    "adaptive_best_hour",
)
