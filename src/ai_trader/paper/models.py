"""Paper-only order, fill, and position models.

Never sent to a broker. Never live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ai_trader.types import utc_now_iso


class PaperAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class FillReason(str, Enum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    TARGET = "TARGET"
    CLOSE = "CLOSE"
    DAY_END = "DAY_END"


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    side: str
    quantity: float
    requested_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    timestamp: str
    status: str = OrderStatus.PENDING.value
    reason: str = "paper"
    source: str = "paper-sim"
    filled_price: Optional[float] = None
    filled_at: Optional[str] = None
    signal_bar: int = 0
    decision_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "requested_price": self.requested_price,
            "filled_price": self.filled_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "timestamp": self.timestamp,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "filled_at": self.filled_at,
            "signal_bar": self.signal_bar,
        }


@dataclass
class PaperFill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: str
    reason: str
    spread: float
    slippage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "spread": self.spread,
            "slippage": self.slippage,
        }


@dataclass
class PaperPosition:
    symbol: str
    quantity: float
    average_entry: float
    current_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    entry_timestamp: str
    order_id: str
    side: str = "LONG"
    exit_timestamp: Optional[str] = None
    realised_pnl: float = 0.0
    open: bool = True

    @property
    def position_value(self) -> float:
        return round(self.quantity * self.current_price, 2)

    @property
    def unrealised_pnl(self) -> float:
        if not self.open:
            return 0.0
        return round((self.current_price - self.average_entry) * self.quantity, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "average_entry": self.average_entry,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealised_pnl": self.unrealised_pnl,
            "realised_pnl": self.realised_pnl,
            "position_value": self.position_value,
            "entry_timestamp": self.entry_timestamp,
            "exit_timestamp": self.exit_timestamp,
            "open": self.open,
            "order_id": self.order_id,
        }
