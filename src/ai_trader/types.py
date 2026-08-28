"""Shared types. Kept small and serialisable."""

from __future__ import annotations

from dataclasses import dataclass, field
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
class MarketBar:
    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MarketSnapshot:
    as_of: str
    bars: tuple[MarketBar, ...]
    source: str
    notes: str = ""


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
    created_at: str = field(default_factory=utc_now_iso)


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
