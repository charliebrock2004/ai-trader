"""Paper prediction-market adapter.

Fills against a **real observed order book** — crossing the spread, walking
levels, and partially filling when the book runs out. What it never does is
send an order anywhere. ``live`` is False and there is no code path that makes
it True.

The book comes from a ``BookSource``: in live paper trading that is a read-only
venue feed, in replay it is a recorded snapshot, in tests it is a fixture.
Whichever it is, the fill mechanics are identical, which is what makes replay
reproduce live behaviour.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ai_trader.clock import Clock, default_clock
from ai_trader.markets.base import (
    Contract,
    ContractFill,
    MarketDataError,
    OrderBook,
    OrderResult,
)

#: A source of books and contracts. Anything with these two callables works.
BookSource = Callable[[str], OrderBook]


class PaperPredictionMarket:
    """Depth-aware paper venue. Never places a real order."""

    name = "paper-prediction-market"
    live = False

    def __init__(
        self,
        *,
        contracts: Optional[dict[str, Contract]] = None,
        book_source: Optional[BookSource] = None,
        clock: Optional[Clock] = None,
        venue: str = "paper",
    ) -> None:
        self.venue = venue
        self.clock = clock or default_clock()
        self._contracts: dict[str, Contract] = dict(contracts or {})
        self._book_source = book_source
        self._books: dict[str, OrderBook] = {}
        self._fills: list[ContractFill] = []
        self._orders: dict[str, OrderResult] = {}
        self._idempotency: dict[str, str] = {}
        self._seq = 0

    # -- registration ------------------------------------------------------
    def register(self, contract: Contract) -> None:
        self._contracts[contract.ticker] = contract

    def set_book(self, book: OrderBook) -> None:
        """Record an observed book. Used by replay and by the live paper loop."""
        self._books[book.ticker] = book

    # -- adapter surface ---------------------------------------------------
    def discover(self, *, event_key: Optional[str] = None) -> list[Contract]:
        rows = list(self._contracts.values())
        if event_key:
            rows = [c for c in rows if c.event_key == event_key]
        return sorted(rows, key=lambda c: c.ticker)

    def rules(self, ticker: str) -> Contract:
        contract = self._contracts.get(ticker)
        if contract is None:
            raise MarketDataError(
                f"Unknown contract {ticker!r}. Refusing to trade rules we do not hold.",
                failure="unknown_contract",
            )
        return contract

    def orderbook(self, ticker: str) -> OrderBook:
        if self._book_source is not None:
            book = self._book_source(ticker)
            if book is not None:
                self._books[ticker] = book
        book = self._books.get(ticker)
        if book is None:
            raise MarketDataError(
                f"No order book for {ticker!r}.", failure="unavailable"
            )
        if not book.asks:
            raise MarketDataError(
                f"Order book for {ticker!r} has no offers. Nothing to buy.",
                failure="no_liquidity",
            )
        if book.best_bid is not None and book.best_ask is not None:
            if book.best_ask < book.best_bid:
                raise MarketDataError(
                    f"Crossed book for {ticker!r}: ask {book.best_ask} below bid "
                    f"{book.best_bid}.",
                    failure="malformed",
                )
        return book

    def submit(
        self,
        *,
        ticker: str,
        contracts: int,
        side: str = "YES",
        limit_price: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> OrderResult:
        """Fill against the observed book. Partial fills are a normal result."""
        if idempotency_key and idempotency_key in self._idempotency:
            return self._orders[self._idempotency[idempotency_key]]

        contract = self.rules(ticker)
        self._seq += 1
        order_id = f"CON-{self._seq:05d}"

        if side != "YES":
            return self._record(
                order_id,
                idempotency_key,
                OrderResult(
                    ok=False, order_id=order_id, ticker=ticker, status="REJECTED",
                    reason="Only YES-side entries are supported in this build.",
                    requested_contracts=int(contracts),
                ),
            )
        if contracts < contract.min_order:
            return self._record(
                order_id,
                idempotency_key,
                OrderResult(
                    ok=False, order_id=order_id, ticker=ticker, status="REJECTED",
                    reason=f"Below the venue minimum of {contract.min_order} contracts.",
                    requested_contracts=int(contracts),
                ),
            )
        if contracts > contract.max_order:
            return self._record(
                order_id,
                idempotency_key,
                OrderResult(
                    ok=False, order_id=order_id, ticker=ticker, status="REJECTED",
                    reason=f"Above the venue maximum of {contract.max_order} contracts.",
                    requested_contracts=int(contracts),
                ),
            )

        book = self.orderbook(ticker)
        filled, average, levels = book.walk_asks(int(contracts))
        if filled == 0:
            return self._record(
                order_id,
                idempotency_key,
                OrderResult(
                    ok=False, order_id=order_id, ticker=ticker, status="REJECTED",
                    reason="No depth available at any price.",
                    requested_contracts=int(contracts),
                ),
            )
        if limit_price is not None and average > limit_price + 1e-9:
            # Re-walk within the limit rather than paying through it.
            capped = [lvl for lvl in book.asks if lvl.price <= limit_price + 1e-9]
            capped_book = OrderBook(
                ticker=book.ticker,
                observed_at=book.observed_at,
                bids=book.bids,
                asks=tuple(capped),
                source=book.source,
            )
            filled, average, levels = capped_book.walk_asks(int(contracts))
            if filled == 0:
                return self._record(
                    order_id,
                    idempotency_key,
                    OrderResult(
                        ok=False, order_id=order_id, ticker=ticker, status="REJECTED",
                        reason=f"No depth at or below the {limit_price} limit.",
                        requested_contracts=int(contracts),
                    ),
                )

        premium = round(sum(lvl.price * lvl.contracts for lvl in levels), 6)
        fee = contract.fee_model.trade_fee(contracts=filled, price=average)
        fill = ContractFill(
            fill_id=f"CFL-{self._seq:05d}",
            order_id=order_id,
            ticker=ticker,
            side=side,
            contracts=filled,
            price=average,
            premium=premium,
            fee=fee,
            quote_currency=contract.quote_currency,
            timestamp=self.clock.now_iso(),
            levels=tuple(levels),
        )
        self._fills.append(fill)
        status = "FILLED" if filled == int(contracts) else "PARTIAL"
        return self._record(
            order_id,
            idempotency_key,
            OrderResult(
                ok=True,
                order_id=order_id,
                ticker=ticker,
                status=status,
                reason=(
                    "Filled against the observed book."
                    if status == "FILLED"
                    else f"Book held only {filled} of {contracts} contracts."
                ),
                requested_contracts=int(contracts),
                filled_contracts=filled,
                average_price=average,
                premium=premium,
                fee=fee,
                fill=fill,
            ),
        )

    def _record(
        self, order_id: str, key: Optional[str], result: OrderResult
    ) -> OrderResult:
        self._orders[order_id] = result
        if key:
            self._idempotency[key] = order_id
        return result

    def positions(self) -> list[dict[str, Any]]:
        """The venue holds no positions of its own; the ledger is authoritative."""
        return []

    def fills(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self._fills]

    def reconcile(self, ledger: Any = None) -> dict[str, Any]:
        """Compare our fills against the ledger's positions.

        A divergence here means the ledger and the venue disagree about what
        was traded, which must surface loudly rather than be papered over.
        """
        venue_contracts: dict[str, int] = {}
        for fill in self._fills:
            venue_contracts[fill.ticker] = venue_contracts.get(fill.ticker, 0) + fill.contracts
        ledger_contracts: dict[str, int] = {}
        if ledger is not None:
            for position in list(ledger.open_positions()) + list(getattr(ledger, "closed", [])):
                ledger_contracts[position.ticker] = (
                    ledger_contracts.get(position.ticker, 0) + position.contracts
                )
        mismatches = [
            {
                "ticker": ticker,
                "venue_contracts": venue_contracts.get(ticker, 0),
                "ledger_contracts": ledger_contracts.get(ticker, 0),
            }
            for ticker in sorted(set(venue_contracts) | set(ledger_contracts))
            if venue_contracts.get(ticker, 0) != ledger_contracts.get(ticker, 0)
        ]
        return {
            "ok": not mismatches,
            "venue": self.venue,
            "live": False,
            "checked": sorted(set(venue_contracts) | set(ledger_contracts)),
            "mismatches": mismatches,
        }

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "venue": self.venue,
            "ready": True,
            "live": False,
            "contracts": len(self._contracts),
            "books": len(self._books),
            "fills": len(self._fills),
            "notes": (
                "Paper venue. Fills walk the observed book and can fill partially. "
                "No order ever leaves this process."
            ),
        }
