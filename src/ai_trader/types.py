"""Shared types. Kept small and serialisable."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class TradingMode(str, Enum):
    SIMULATE = "simulate"
    PAPER = "paper"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. Timestamp is the bar open, UTC ISO-8601."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandleSeries:
    symbol: str
    timeframe: str
    scenario: str
    seed: int
    candles: tuple[Candle, ...]
    source: str = "simulated"

    def last(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "scenario": self.scenario,
            "seed": self.seed,
            "source": self.source,
            "bar_count": len(self.candles),
            "candles": [c.to_dict() for c in self.candles],
        }


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "5m"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: str
    bars: tuple[MarketBar, ...]
    source: str
    notes: str = ""
    timeframe: str = "5m"
    series: tuple[CandleSeries, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "source": self.source,
            "notes": self.notes,
            "timeframe": self.timeframe,
            "bars": [b.to_dict() for b in self.bars],
            "series": [s.to_dict() for s in self.series],
        }


@dataclass(frozen=True)
class MarketAnalysis:
    """Read-only technical snapshot. Never an order."""

    symbol: str
    timeframe: str
    scenario: str
    as_of: str
    bar_count: int
    current_price: Optional[float]
    recent_high: Optional[float]
    recent_low: Optional[float]
    trend: str
    last_abs: Optional[float]
    last_pct: Optional[float]
    lookbacks: dict[str, Optional[float]]
    sma_5: Optional[float]
    sma_10: Optional[float]
    sma_20: Optional[float]
    sma_50: Optional[float]
    slope_5: Optional[float]
    slope_10: Optional[float]
    slope_20: Optional[float]
    price_vs_sma_5: Optional[float]
    price_vs_sma_10: Optional[float]
    price_vs_sma_20: Optional[float]
    price_vs_sma_50: Optional[float]
    last_range: Optional[float]
    last_range_pct: Optional[float]
    average_range: Optional[float]
    average_range_pct: Optional[float]
    rolling_volatility: Optional[float]
    last_volume: Optional[float]
    average_volume: Optional[float]
    volume_vs_average: Optional[float]
    notes: str
    source: str = "technical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "scenario": self.scenario,
            "as_of": self.as_of,
            "bar_count": self.bar_count,
            "current_price": self.current_price,
            "recent_high": self.recent_high,
            "recent_low": self.recent_low,
            "trend": self.trend,
            "returns": {
                "last_abs": self.last_abs,
                "last_pct": self.last_pct,
                "lookbacks": self.lookbacks,
            },
            "sma": {
                "sma_5": self.sma_5,
                "sma_10": self.sma_10,
                "sma_20": self.sma_20,
                "sma_50": self.sma_50,
                "slope_5": self.slope_5,
                "slope_10": self.slope_10,
                "slope_20": self.slope_20,
                "price_vs_sma_5": self.price_vs_sma_5,
                "price_vs_sma_10": self.price_vs_sma_10,
                "price_vs_sma_20": self.price_vs_sma_20,
                "price_vs_sma_50": self.price_vs_sma_50,
            },
            "volatility": {
                "last_range": self.last_range,
                "last_range_pct": self.last_range_pct,
                "average_range": self.average_range,
                "average_range_pct": self.average_range_pct,
                "rolling_stdev": self.rolling_volatility,
            },
            "volume": {
                "last": self.last_volume,
                "average": self.average_volume,
                "vs_average": self.volume_vs_average,
            },
            "notes": self.notes,
            "source": self.source,
        }


@dataclass(frozen=True)
class AnalysisBundle:
    as_of: str
    analyses: tuple[MarketAnalysis, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "analyses": [item.to_dict() for item in self.analyses],
        }


@dataclass(frozen=True)
class Decision:
    """An AI proposal. Never an order."""

    symbol: str
    action: Action
    confidence: Optional[float]
    rationale: str
    model: str
    raw_response: str = ""
    market_snapshot_json: str = ""
    analysis_ref: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value if isinstance(self.action, Action) else self.action
        return {
            "symbol": self.symbol,
            "action": action,
            "confidence": self.confidence,
            "reasoning": self.rationale,
            "timestamp": self.created_at,
            "analysis_ref": self.analysis_ref,
            "model": self.model,
        }


@dataclass(frozen=True)
class PaperAccountState:
    """Simulated paper account. Never a live brokerage account."""

    currency: str
    starting_cash: float
    cash: float
    buying_power: float
    account_equity: float
    invested_value: float
    realised_pnl: float
    unrealised_pnl: float
    total_pnl: float
    positions: tuple
    fill_count: int
    source: str
    as_of: str
    drawdown: float = 0.0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "account_equity": self.account_equity,
            "invested_value": self.invested_value,
            "realised_pnl": self.realised_pnl,
            "unrealised_pnl": self.unrealised_pnl,
            "total_pnl": self.total_pnl,
            "positions": list(self.positions),
            "fill_count": self.fill_count,
            "source": self.source,
            "as_of": self.as_of,
            "drawdown": self.drawdown,
            "daily_pnl": self.daily_pnl,
            "peak_equity": self.peak_equity,
            "halted": self.halted,
            "live": False,
        }


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str
    max_qty: Optional[float] = None
    decision: Optional[Decision] = None


@dataclass(frozen=True)
class IntendedOrder:
    """What would be sent to a broker. Not an executed trade."""

    symbol: str
    side: Side
    qty: float
    decision_id: Optional[int] = None
    limit_price: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)
