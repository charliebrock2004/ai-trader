"""Supported bar sizes. Values are seconds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_trader.exceptions import InvalidMarketDataError

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

DEFAULT_TIMEFRAME = "5m"

# Fixed anchor so the same seed always produces the same timestamps.
SERIES_START = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)


def timeframe_seconds(timeframe: str) -> int:
    key = (timeframe or "").strip().lower()
    if key not in TIMEFRAME_SECONDS:
        allowed = ", ".join(TIMEFRAME_SECONDS)
        raise InvalidMarketDataError(
            f"Unknown timeframe '{timeframe}'. Allowed: {allowed}."
        )
    return TIMEFRAME_SECONDS[key]


def bar_time(index: int, *, timeframe: str, start: datetime = SERIES_START) -> datetime:
    if index < 0:
        raise InvalidMarketDataError("Bar index cannot be negative.")
    return start + timedelta(seconds=timeframe_seconds(timeframe) * index)


def iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()
