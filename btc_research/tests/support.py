"""Deterministic candles for tests. Not market data."""

from __future__ import annotations

from btc_research.data.candles import Candle

HOUR = 3_600_000
START = 1_577_836_800_000  # 2020-01-01T00:00:00Z


def make_candles(closes: list[float], opens: list[float] | None = None, start_ms: int = START) -> list[Candle]:
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        open_ = close if opens is None else opens[index]
        high = max(open_, close) + 0.5
        low = min(open_, close) - 0.5
        if low <= 0:
            low = min(open_, close)
        rows.append(
            Candle(
                open_time_ms=start_ms + index * HOUR,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1.0,
                close_time_ms=start_ms + index * HOUR + HOUR - 1,
            )
        )
    return rows
