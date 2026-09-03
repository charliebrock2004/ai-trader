"""Prediction-market abstraction.

A binary contract is not a share. It costs ``price`` (0-1), it settles at 1 or
0, and there is no stop loss — an adverse resolution takes the entire premium.
That is why this lives beside the spot ledger rather than inside it.

    premium paid   = contracts x price
    maximum loss   = premium paid            (plus fees)
    maximum payout = contracts x 1
    maximum profit = contracts x (1 - price) (minus fees)

Every adapter is paper. ``PredictionMarketAdapter`` describes the shape a real
venue would implement, but nothing in this repository submits a real order and
no adapter here is wired to a funded account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from ai_trader.markets.fees import FeeModel, STANDARD_FEES

#: A binary contract's price lives strictly inside (0, 1).
MIN_PRICE = 0.01
MAX_PRICE = 0.99


class MarketDataError(RuntimeError):
    """The venue could not give a usable book or contract. Fail closed."""

    def __init__(self, message: str, *, failure: str = "unavailable") -> None:
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True)
class Contract:
    """One binary market and everything needed to price and resolve it."""

    ticker: str
    question: str
    event_key: str
    resolution_source: str
    resolution_time: str
    settlement_rules: str
    venue: str = "paper"
    quote_currency: str = "USD"
    tick_size: float = 0.01
    min_order: int = 1
    max_order: int = 10_000
    fee_model: FeeModel = field(default=STANDARD_FEES)
    #: The threshold this contract resolves against, when it is a numeric
    #: comparison (e.g. "CPI YoY above 3.0"). None for non-numeric questions.
    strike: Optional[float] = None
    comparison: str = "above"

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive.")
        if self.min_order < 1:
            raise ValueError("min_order must be at least one contract.")
        if self.max_order < self.min_order:
            raise ValueError("max_order cannot be below min_order.")
        if self.comparison not in {"above", "below", "at_or_above", "at_or_below"}:
            raise ValueError(f"Unsupported comparison {self.comparison!r}.")

    def round_price(self, price: float) -> float:
        steps = round(float(price) / self.tick_size)
        return round(max(MIN_PRICE, min(MAX_PRICE, steps * self.tick_size)), 6)

    def resolves_yes(self, observed: float) -> bool:
        """Apply the contract's own comparison to an observed value."""
        if self.strike is None:
            raise MarketDataError(
                f"{self.ticker} has no numeric strike; it cannot be resolved arithmetically.",
                failure="ambiguous",
            )
        value = float(observed)
        if self.comparison == "above":
            return value > self.strike
        if self.comparison == "at_or_above":
            return value >= self.strike
        if self.comparison == "below":
            return value < self.strike
        return value <= self.strike

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "question": self.question,
            "event_key": self.event_key,
            "resolution_source": self.resolution_source,
            "resolution_time": self.resolution_time,
            "settlement_rules": self.settlement_rules,
            "venue": self.venue,
            "quote_currency": self.quote_currency,
            "tick_size": self.tick_size,
            "min_order": self.min_order,
            "max_order": self.max_order,
            "fee_model": self.fee_model.to_dict(),
            "strike": self.strike,
            "comparison": self.comparison,
        }


@dataclass(frozen=True)
class BookLevel:
    price: float
    contracts: int

    def to_dict(self) -> dict[str, Any]:
        return {"price": self.price, "contracts": self.contracts}


@dataclass(frozen=True)
class OrderBook:
    """A YES-side book. ``bids`` are buyers of YES, ``asks`` are sellers.

    Depth is not decoration. A £100 account meeting a book with 30 contracts at
    the touch is a real constraint, and :meth:`walk_asks` is what expresses it.
    """

    ticker: str
    observed_at: str
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    source: str = "paper"

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return round((self.best_bid + self.best_ask) / 2.0, 6)

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return round(self.best_ask - self.best_bid, 6)

    @property
    def top_depth(self) -> int:
        return self.asks[0].contracts if self.asks else 0

    @property
    def total_ask_depth(self) -> int:
        return sum(level.contracts for level in self.asks)

    def walk_asks(self, contracts: int) -> tuple[int, float, list[BookLevel]]:
        """Consume the ask side. Returns (filled, average price, levels used).

        Partial fills are normal, not an error: if the book cannot supply the
        requested size, the caller gets what the book actually held.
        """
        wanted = int(contracts)
        if wanted <= 0:
            return 0, 0.0, []
        filled = 0
        cost = 0.0
        used: list[BookLevel] = []
        for level in self.asks:
            if filled >= wanted:
                break
            take = min(level.contracts, wanted - filled)
            if take <= 0:
                continue
            filled += take
            cost += take * level.price
            used.append(BookLevel(price=level.price, contracts=take))
        if filled == 0:
            return 0, 0.0, []
        return filled, round(cost / filled, 6), used

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "observed_at": self.observed_at,
            "bids": [level.to_dict() for level in self.bids],
            "asks": [level.to_dict() for level in self.asks],
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid": self.mid,
            "spread": self.spread,
            "top_depth": self.top_depth,
            "total_ask_depth": self.total_ask_depth,
            "source": self.source,
        }


@dataclass(frozen=True)
class ContractFill:
    fill_id: str
    order_id: str
    ticker: str
    side: str
    contracts: int
    price: float
    premium: float
    fee: float
    quote_currency: str
    timestamp: str
    levels: tuple[BookLevel, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "contracts": self.contracts,
            "price": self.price,
            "premium": self.premium,
            "fee": self.fee,
            "quote_currency": self.quote_currency,
            "timestamp": self.timestamp,
            "levels": [level.to_dict() for level in self.levels],
        }


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: str
    ticker: str
    status: str
    reason: str
    requested_contracts: int
    filled_contracts: int = 0
    average_price: float = 0.0
    premium: float = 0.0
    fee: float = 0.0
    fill: Optional[ContractFill] = None
    live: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "order_id": self.order_id,
            "ticker": self.ticker,
            "status": self.status,
            "reason": self.reason,
            "requested_contracts": self.requested_contracts,
            "filled_contracts": self.filled_contracts,
            "average_price": self.average_price,
            "premium": self.premium,
            "fee": self.fee,
            "fill": self.fill.to_dict() if self.fill else None,
            "live": False,
        }


@runtime_checkable
class PredictionMarketAdapter(Protocol):
    """What a venue must provide. Every implementation here is paper."""

    name: str
    live: bool

    def discover(self, *, event_key: Optional[str] = None) -> list[Contract]:
        """Contracts currently tradeable, optionally filtered to one event."""

    def rules(self, ticker: str) -> Contract:
        """The contract's full resolution terms."""

    def orderbook(self, ticker: str) -> OrderBook:
        """Current book. Raises MarketDataError rather than guessing."""

    def submit(
        self,
        *,
        ticker: str,
        contracts: int,
        side: str = "YES",
        limit_price: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> OrderResult:
        """Place an order. Paper adapters fill against the observed book."""

    def positions(self) -> list[dict[str, Any]]: ...

    def fills(self) -> list[dict[str, Any]]: ...

    def reconcile(self) -> dict[str, Any]:
        """Compare the venue's view against ours and report any divergence."""

    def health(self) -> dict[str, Any]: ...
