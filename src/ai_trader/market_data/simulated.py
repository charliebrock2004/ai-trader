"""In-process simulated feed. No network. Deterministic for a given seed."""

from __future__ import annotations

from datetime import datetime

from ai_trader.market_data.base import MarketDataProvider
from ai_trader.market_data.generator import DEFAULT_LIMIT, DEFAULT_SEED, generate_series
from ai_trader.market_data.scenarios import SCENARIOS, SYMBOL_SCENARIOS
from ai_trader.market_data.timeframes import DEFAULT_TIMEFRAME, SERIES_START
from ai_trader.types import CandleSeries


class SimulatedMarketData(MarketDataProvider):
    name = "simulated"

    def __init__(
        self,
        *,
        seed: int = DEFAULT_SEED,
        timeframe: str = DEFAULT_TIMEFRAME,
        start: datetime = SERIES_START,
    ) -> None:
        self.seed = seed
        self.timeframe = timeframe
        self.start = start

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str | None = None,
        limit: int = DEFAULT_LIMIT,
        scenario: str | None = None,
    ) -> CandleSeries:
        return generate_series(
            symbol,
            timeframe=timeframe or self.timeframe,
            limit=limit,
            seed=self.seed,
            scenario=scenario,
            start=self.start,
            source=self.name,
        )

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "seed": self.seed,
            "timeframe": self.timeframe,
            "scenarios": sorted(SCENARIOS),
            "symbols": dict(SYMBOL_SCENARIOS),
            "notes": "Deterministic simulated OHLCV. No external market data.",
        }
