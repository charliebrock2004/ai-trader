"""Reject malformed OHLCV before it reaches the pipeline."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ai_trader.exceptions import InvalidMarketDataError
from ai_trader.market_data.timeframes import timeframe_seconds
from ai_trader.types import Candle, CandleSeries, MarketBar, MarketSnapshot

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._-]{0,15}$")
MAX_BARS = 500


def parse_utc(timestamp: str) -> datetime:
    raw = (timestamp or "").strip()
    if not raw:
        raise InvalidMarketDataError("Candle timestamp is required.")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidMarketDataError(f"Invalid timestamp '{timestamp}'.") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _finite(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InvalidMarketDataError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidMarketDataError(f"{name} must be finite.")
    return number


def validate_symbol(symbol: str) -> str:
    cleaned = (symbol or "").strip().upper()
    if not SYMBOL_RE.match(cleaned):
        raise InvalidMarketDataError(
            f"Invalid symbol '{symbol}'. Use 1–16 characters: A-Z, 0-9, ., _, -."
        )
    return cleaned


def validate_candle(candle: Candle, *, symbol: str | None = None) -> Candle:
    ts = parse_utc(candle.timestamp)
    open_ = _finite("open", candle.open)
    high = _finite("high", candle.high)
    low = _finite("low", candle.low)
    close = _finite("close", candle.close)
    volume = _finite("volume", candle.volume)
    label = f" {symbol}" if symbol else ""
    if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
        raise InvalidMarketDataError(f"Prices must be positive.{label}")
    if volume < 0:
        raise InvalidMarketDataError(f"Volume cannot be negative.{label}")
    if high + 1e-12 < max(open_, close, low):
        raise InvalidMarketDataError(
            f"High must be >= open, close, and low at {ts.isoformat()}.{label}"
        )
    if low - 1e-12 > min(open_, close, high):
        raise InvalidMarketDataError(
            f"Low must be <= open, close, and high at {ts.isoformat()}.{label}"
        )
    return candle


def validate_series(series: CandleSeries) -> CandleSeries:
    symbol = validate_symbol(series.symbol)
    timeframe_seconds(series.timeframe)
    if not series.candles:
        raise InvalidMarketDataError(f"Series {symbol} has no candles.")
    if len(series.candles) > MAX_BARS:
        raise InvalidMarketDataError(
            f"Series {symbol} has {len(series.candles)} bars; max is {MAX_BARS}."
        )
    previous: datetime | None = None
    step = timeframe_seconds(series.timeframe)
    for candle in series.candles:
        validate_candle(candle, symbol=symbol)
        moment = parse_utc(candle.timestamp)
        if previous is not None:
            if moment <= previous:
                raise InvalidMarketDataError(
                    f"Timestamps must be strictly increasing for {symbol}."
                )
            delta = int((moment - previous).total_seconds())
            if delta != step:
                raise InvalidMarketDataError(
                    f"Bar spacing for {symbol} is {delta}s, expected {step}s."
                )
        previous = moment
    return series


def validate_snapshot(snapshot: MarketSnapshot) -> MarketSnapshot:
    if snapshot.series:
        for series in snapshot.series:
            validate_series(series)
    for bar in snapshot.bars:
        validate_symbol(bar.symbol)
        validate_candle(
            Candle(
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            ),
            symbol=bar.symbol,
        )
    return snapshot


def bar_from_series(series: CandleSeries) -> MarketBar:
    last = series.last()
    if last is None:
        raise InvalidMarketDataError(f"Series {series.symbol} is empty.")
    return MarketBar(
        symbol=series.symbol,
        timestamp=last.timestamp,
        open=last.open,
        high=last.high,
        low=last.low,
        close=last.close,
        volume=last.volume,
        timeframe=series.timeframe,
    )
