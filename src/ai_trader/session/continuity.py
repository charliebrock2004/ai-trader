"""Candle-tape continuity across free-host restarts.

The SMA 10/20 filter must see the same closed bars it would have seen if the
worker had stayed up. Free Render sleeps and the process dies. On wake we
reload enough history for the indicators, treat that history as warm-up only,
and only trade candles strictly after the last successfully processed
timestamp. Exact crossover logic is unchanged. Live trading stays impossible.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ai_trader.analysis.indicators import sma
from ai_trader.benchmark.strategies import SMA_FAST, SMA_SLOW
from ai_trader.market_data.validation import parse_utc

#: Closed bars loaded on every continuous public start so SMA20 is the same
#: function of history it is on a full tape, not a 24-bar stub.
INDICATOR_HISTORY_BARS = 60
#: Fetch window: 60 bars of indicator context plus catch-up after a host sleep.
#:
#: This is the hard limit on how long the desk may be asleep without losing
#: evidence. Any candle older than this window is gone: the desk never sees it,
#: never decides on it, and never records why. At 120 bars that was ten hours,
#: and the wake schedule is not reliable enough to promise that — the host
#: sleeps when idle and the external ping is throttled, so multi-hour gaps are
#: normal rather than exceptional.
#:
#: 280 five-minute bars is just over 23 hours, and stays inside the ~300 the
#: public feed returns in one request. It costs one slightly larger fetch per
#: wake and buys a full day of tolerance.
CONTINUOUS_FETCH_BARS = 280


def fetch_limit(*, bars: int, continuous: bool, source: str) -> int:
    """How many candles to request. Simulated/blocking sessions keep ``bars``."""
    limit = max(2, int(bars))
    if continuous and (source or "").strip().lower() == "public":
        return max(limit, CONTINUOUS_FETCH_BARS, INDICATOR_HISTORY_BARS)
    return limit


def resolve_trade_from_index(
    candles: Sequence[Any],
    last_processed_ts: Optional[str],
) -> int:
    """First live index. Bars before it warm indicators and must not trade.

    No persisted timestamp → the whole fetch is a baseline. We do not
    retroactively trade historical crossovers.
    """
    count = len(candles)
    if count == 0:
        return 0
    if not last_processed_ts:
        return count
    try:
        cutoff = parse_utc(last_processed_ts)
    except (TypeError, ValueError):
        return count
    first_live: Optional[int] = None
    for index, candle in enumerate(candles):
        try:
            stamp = parse_utc(getattr(candle, "timestamp", None))
        except (TypeError, ValueError):
            continue
        if stamp > cutoff:
            first_live = index
            break
    if first_live is None:
        return count
    return first_live


def baseline_timestamp(candles: Sequence[Any], trade_from_index: int) -> Optional[str]:
    """Timestamp of the last warm-up bar, used as the first-session baseline."""
    if not candles:
        return None
    if trade_from_index <= 0:
        return None
    index = min(int(trade_from_index), len(candles)) - 1
    if index < 0:
        return None
    stamp = getattr(candles[index], "timestamp", None)
    return str(stamp) if stamp else None


def indicator_snapshot(candles: Sequence[Any]) -> dict[str, Any]:
    """Current SMA 10/20 on the loaded tape. Not a trade signal."""
    closes = [float(getattr(c, "close")) for c in candles]
    fast = sma(closes, SMA_FAST)
    slow = sma(closes, SMA_SLOW)
    if fast is None or slow is None:
        relationship = "warming"
    elif fast > slow:
        relationship = "fast_above_slow"
    elif fast < slow:
        relationship = "fast_below_slow"
    else:
        relationship = "equal"
    latest = candles[-1] if candles else None
    return {
        "sma10": None if fast is None else round(float(fast), 4),
        "sma20": None if slow is None else round(float(slow), 4),
        "sma_relationship": relationship,
        "indicator_history_bars": len(closes),
        "latest_candle_ts": getattr(latest, "timestamp", None) if latest else None,
        "latest_close": None if latest is None else float(getattr(latest, "close")),
    }


__all__ = [
    "CONTINUOUS_FETCH_BARS",
    "INDICATOR_HISTORY_BARS",
    "baseline_timestamp",
    "fetch_limit",
    "indicator_snapshot",
    "resolve_trade_from_index",
]
