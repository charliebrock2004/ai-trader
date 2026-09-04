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


# ---------------------------------------------------------------------------
# Intraday indicators
#
# Added for the live short-term detector. Same contract as the rest of this
# module: pure functions over price history, no I/O, and ``None`` whenever the
# series is too short rather than a guessed value.
# ---------------------------------------------------------------------------
EMA_FAST = 20
EMA_SLOW = 50
ATR_WINDOW = 14
RSI_WINDOW = 14


def ema(values: list[float], window: int) -> Optional[float]:
    """Exponential moving average.

    Preferred over SMA for the live detector: it reacts to a turn several bars
    sooner, which on 5-minute bars is the difference between joining a move and
    reading about it.
    """
    if window <= 0 or len(values) < window:
        return None
    multiplier = 2.0 / (window + 1.0)
    average = mean(values[:window])
    if average is None:
        return None
    for value in values[window:]:
        average = (value - average) * multiplier + average
    return average


def true_ranges(
    highs: list[float], lows: list[float], closes: list[float]
) -> list[float]:
    """True range per bar. The first bar has no previous close, so it is skipped."""
    out: list[float] = []
    for index in range(1, min(len(highs), len(lows), len(closes))):
        previous_close = closes[index - 1]
        out.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - previous_close),
                abs(lows[index] - previous_close),
            )
        )
    return out


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    window: int = ATR_WINDOW,
) -> Optional[float]:
    """Average true range: the size of a normal bar, in price units.

    Stops and targets are set from this rather than a fixed percentage. A fixed
    percentage is the same distance in a dead market and a violent one, which
    makes it simultaneously too tight and too wide depending on the day.
    """
    ranges = true_ranges(highs, lows, closes)
    if len(ranges) < window:
        return None
    return mean(ranges[-window:])


def rsi(closes: list[float], window: int = RSI_WINDOW) -> Optional[float]:
    """Wilder's RSI, 0-100. Used to tell a healthy pullback from a collapse."""
    if len(closes) < window + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = mean(gains[:window])
    average_loss = mean(losses[:window])
    if average_gain is None or average_loss is None:
        return None
    for index in range(window, len(gains)):
        average_gain = (average_gain * (window - 1) + gains[index]) / window
        average_loss = (average_loss * (window - 1) + losses[index]) / window
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + strength))


def highest(values: list[float], window: int) -> Optional[float]:
    if window <= 0 or len(values) < window:
        return None
    return max(values[-window:])


def lowest(values: list[float], window: int) -> Optional[float]:
    if window <= 0 or len(values) < window:
        return None
    return min(values[-window:])


def position_in_range(value: float, low: Optional[float], high: Optional[float]) -> Optional[float]:
    """Where ``value`` sits between ``low`` and ``high``, as 0.0-1.0."""
    if low is None or high is None:
        return None
    span = high - low
    if span <= 0:
        return None
    return max(0.0, min(1.0, (value - low) / span))
