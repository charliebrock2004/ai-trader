"""Train / validation / out-of-sample periods.

Simulated data uses independent seeds so splits do not leak into each other.
Historical OHLCV is a hook only — it is not connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ai_trader.exceptions import HistoricalDataNotConfiguredError
from ai_trader.market_data.generator import generate_series
from ai_trader.market_data.scenarios import DEFAULT_SYMBOLS
from ai_trader.types import CandleSeries

HEADLINE_SPLIT = "out_of_sample"
DEFAULT_LIMIT = 60


@dataclass(frozen=True)
class BenchmarkPeriod:
    """One labelled window. start/end are reserved for a future historical feed."""

    name: str
    seed: int
    limit: int = DEFAULT_LIMIT
    source: str = "simulated"
    timeframe: str = "5m"
    start: Optional[str] = None
    end: Optional[str] = None

    def public(self) -> dict:
        roles = {
            "training": "Development data. Strategies are frozen; this split is not used to tune.",
            "validation": "Held-out check. Not used to change parameters.",
            "out_of_sample": "Final test. Never used to modify a strategy.",
        }
        return {
            "name": self.name,
            "seed": self.seed,
            "limit": self.limit,
            "source": self.source,
            "timeframe": self.timeframe,
            "start": self.start,
            "end": self.end,
            "role": roles.get(self.name, self.name),
        }


DEFAULT_PERIODS: tuple[BenchmarkPeriod, ...] = (
    BenchmarkPeriod(name="training", seed=101),
    BenchmarkPeriod(name="validation", seed=202),
    BenchmarkPeriod(name="out_of_sample", seed=303),
)

DEFAULT_MARKETS: tuple[str, ...] = DEFAULT_SYMBOLS


def load_series(symbol: str, period: BenchmarkPeriod) -> CandleSeries:
    """Load candles for a period. Historical source is refused (stays offline)."""
    if period.source != "simulated":
        raise HistoricalDataNotConfiguredError(
            "Historical OHLCV is not connected. Benchmark periods stay on "
            "simulated data until a read-only historical provider is added."
        )
    return generate_series(
        symbol,
        timeframe=period.timeframe,
        limit=period.limit,
        seed=period.seed,
        source="simulated",
    )
