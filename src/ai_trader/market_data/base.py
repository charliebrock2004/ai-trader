"""Market data interface. Swap SimulatedMarketData for a live provider later."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_trader.market_data.timeframes import DEFAULT_TIMEFRAME
from ai_trader.market_data.validation import bar_from_series, validate_snapshot
from ai_trader.types import CandleSeries, MarketSnapshot, utc_now_iso


class MarketDataProvider(ABC):
    """Anything that can supply OHLCV. Implementations must not place orders."""

    name: str = "base"

    @abstractmethod
    def candles(
        self,
        symbol: str,
        *,
        timeframe: str = DEFAULT_TIMEFRAME,
        limit: int = 48,
        scenario: str | None = None,
    ) -> CandleSeries:
        """Return a validated, time-ordered candle series."""

    def snapshot(
        self,
        symbols: list[str],
        *,
        timeframe: str = DEFAULT_TIMEFRAME,
        limit: int = 48,
    ) -> MarketSnapshot:
        series = tuple(
            self.candles(symbol, timeframe=timeframe, limit=limit) for symbol in symbols
        )
        bars = tuple(bar_from_series(item) for item in series)
        snapshot = MarketSnapshot(
            as_of=utc_now_iso(),
            bars=bars,
            source=self.name,
            notes=self.health().get("notes", ""),
            timeframe=timeframe,
            series=series,
        )
        return validate_snapshot(snapshot)

    def health(self) -> dict:
        return {"name": self.name, "ready": False, "notes": "Interface only."}
