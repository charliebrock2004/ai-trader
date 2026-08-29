"""Configurable paper-session settings. Frozen a priori. Not live."""

from __future__ import annotations

from dataclasses import dataclass

from ai_trader.account.simulated import STARTING_CASH
from ai_trader.exceptions import HistoricalDataNotConfiguredError, InvalidMarketDataError
from ai_trader.market_data.timeframes import TIMEFRAME_SECONDS, timeframe_seconds


BANNER = "PAPER SIMULATION — NO REAL TRADING"
DEFAULT_SYMBOL = "SIM-UP"
DEFAULT_BARS = 24
DEFAULT_FREQUENCY = 8
DEFAULT_WARMUP = 8
ALLOWED_SOURCES = frozenset({"simulated", "public"})


@dataclass(frozen=True)
class PaperSessionConfig:
    starting_balance: float = STARTING_CASH
    symbol: str = DEFAULT_SYMBOL
    timeframe: str = "5m"
    bars: int = DEFAULT_BARS
    grok_frequency: int = DEFAULT_FREQUENCY
    warmup: int = DEFAULT_WARMUP
    seed: int = 42
    source: str = "simulated"
    flatten_at_end: bool = True
    continuous: bool = False

    def validate(self) -> "PaperSessionConfig":
        source = (self.source or "").strip().lower()
        if source not in ALLOWED_SOURCES:
            raise HistoricalDataNotConfiguredError(
                f"Market-data source '{self.source}' is not wired. "
                "Allowed: simulated, public."
            )
        timeframe_seconds(self.timeframe)
        if self.bars < 2:
            raise InvalidMarketDataError("Session duration must be at least 2 candles.")
        if self.grok_frequency < 1:
            raise InvalidMarketDataError("Grok decision frequency must be at least 1 bar.")
        if self.warmup < 0:
            raise InvalidMarketDataError("Warmup cannot be negative.")
        if self.starting_balance <= 0:
            raise InvalidMarketDataError("Starting balance must be positive.")
        return self

    def public(self) -> dict:
        is_public = self.source == "public"
        return {
            "starting_balance": self.starting_balance,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "duration_candles": self.bars,
            "grok_frequency": self.grok_frequency,
            "warmup": self.warmup,
            "seed": self.seed,
            "source": self.source,
            "flatten_at_end": self.flatten_at_end,
            "continuous": self.continuous,
            "live": False,
            "real_market_data": is_public,
            "allowed_timeframes": sorted(TIMEFRAME_SECONDS),
            "allowed_public_symbols": ["BTC-USD", "ETH-USD"],
        }
