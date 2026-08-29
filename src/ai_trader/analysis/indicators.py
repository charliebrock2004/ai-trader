"""Read-only indicator math. No I/O. No execution. Not a trade signal."""

from __future__ import annotations

import math
from typing import Optional

SMA_WINDOWS: tuple[int, ...] = (5, 10, 20, 50)
RETURN_LOOKBACKS: tuple[int, ...] = (1, 5, 10, 20)
VOL_WINDOW = 20
RANGE_WINDOW = 20
SLOPE_SHIFT = 5
SLOPE_DEADZONE = 0.0025


def mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def sample_stdev(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    centre = mean(values)
    if centre is None:
        return None
    variance = sum((item - centre) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(variance)


def sma(closes: list[float], window: int) -> Optional[float]:
    if window <= 0 or len(closes) < window:
        return None
    return mean(closes[-window:])


def sma_slope(closes: list[float], window: int, shift: int = SLOPE_SHIFT) -> Optional[float]:
    if window <= 0 or shift <= 0 or len(closes) < window + shift:
        return None
    now = sma(closes, window)
    then = sma(closes[:-shift], window)
    if now is None or then is None or then == 0:
        return None
    return (now - then) / then


def price_vs_sma(price: Optional[float], average: Optional[float]) -> Optional[float]:
    if price is None or average is None or average == 0:
        return None
    return (price - average) / average


def abs_change(closes: list[float], lookback: int = 1) -> Optional[float]:
    if lookback <= 0 or len(closes) <= lookback:
        return None
    return closes[-1] - closes[-(lookback + 1)]


def pct_change(closes: list[float], lookback: int = 1) -> Optional[float]:
    if lookback <= 0 or len(closes) <= lookback:
        return None
    start = closes[-(lookback + 1)]
    if start == 0:
        return None
    return (closes[-1] - start) / start


def one_bar_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for index in range(1, len(closes)):
        prev = closes[index - 1]
        if prev == 0:
            continue
        out.append((closes[index] - prev) / prev)
    return out


def rolling_volatility(closes: list[float], window: int = VOL_WINDOW) -> Optional[float]:
    returns = one_bar_returns(closes)
    if len(returns) < 2:
        return None
    return sample_stdev(returns[-window:])


def classify_trend(
    price: Optional[float],
    anchor_sma: Optional[float],
    slope: Optional[float],
) -> str:
    """Trend from SMA slope. Incomplete series stay UNKNOWN."""
    if price is None or anchor_sma is None or slope is None:
        return "UNKNOWN"
    if slope > SLOPE_DEADZONE:
        return "UP"
    if slope < -SLOPE_DEADZONE:
        return "DOWN"
    return "SIDEWAYS"
