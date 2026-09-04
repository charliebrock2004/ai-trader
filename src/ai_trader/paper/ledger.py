"""Mutable paper ledger. Offline only. No broker, no withdrawals, no leverage.

Accounting units
----------------
Cash, equity and every P&L figure are in the account's **base** currency
(GBP by default). Instrument prices are in the instrument's **quote** currency.
Crossing between them requires an explicit FX rate that has been handed to the
ledger; there is no implicit conversion and no default of 1.0 for a
foreign-quoted instrument.

Trade counting
--------------
``trades_today`` counts *round trips*, not fills: it increments when a position
is closed, not when one is opened. An open position that has not yet been
closed is exposed as ``open_trades`` so the risk engine can still reason about
in-flight exposure.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.account.simulated import CURRENCY, SOURCE, STARTING_CASH
from ai_trader.money import BASE_CURRENCY, money_float, normalise_currency
from ai_trader.paper.models import PaperFill, PaperOrder, PaperPosition
from ai_trader.types import PaperAccountState, utc_now_iso


def _opt_float(value: Any) -> Optional[float]:
    """A stop of ``None`` means no stop; 0.0 would mean "exit at zero"."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value: float) -> float:
    return money_float(value)


class LedgerCurrencyError(RuntimeError):
    """A foreign-quoted fill was attempted without an FX rate. Fail closed."""


class PaperLedger:
    def __init__(
        self,
        *,
        starting_cash: float = STARTING_CASH,
        base_currency: str = CURRENCY,
    ) -> None:
        cash = money(starting_cash)
        self.base_currency = normalise_currency(base_currency)
        self.starting_cash = cash
        self.cash = cash
        self.realised_pnl = 0.0
        self.peak_equity = cash
        self.day_start_equity = cash
        self.day_key = ""
        self.trades_today = 0
        self.round_trips = 0
        self.entries_today = 0
        self.halted = False
        self.positions: dict[str, PaperPosition] = {}
        self.closed_positions: list[PaperPosition] = []
        self.orders: list[PaperOrder] = []
        self.fills: list[PaperFill] = []
        self._order_seq = 0
        self._fill_seq = 0
        #: quote currency -> units of base currency per unit of quote.
        self._fx: dict[str, float] = {self.base_currency: 1.0}

    # -- restart recovery -------------------------------------------------
    def restore(self, *, cash: float, positions: list[dict[str, Any]]) -> int:
        """Re-open positions carried over from a previous process.

        The worker's host restarts often, and a position that does not survive
        that is worse than one that does not exist: the old behaviour rebuilt
        the ledger from marked equity alone, so an open position silently
        became cash at its mark price — no exit fill, no spread, no slippage,
        and the stop loss quietly gone. That is invented P&L, and on a host
        that restarts every few minutes it would have been most of the record.

        Restoring cash and the position separately keeps equity identical while
        leaving the trade genuinely open, still carrying its stop and target.
        """
        self.cash = money(cash)
        restored = 0
        for row in positions or []:
            if not isinstance(row, dict) or not row.get("open", True):
                continue
            symbol = row.get("symbol")
            quantity = float(row.get("quantity") or 0.0)
            if not symbol or quantity <= 0:
                continue
            self.positions[str(symbol)] = PaperPosition(
                symbol=str(symbol),
                quantity=quantity,
                average_entry=float(row.get("average_entry") or 0.0),
                current_price=float(row.get("current_price") or row.get("average_entry") or 0.0),
                stop_loss=_opt_float(row.get("stop_loss")),
                take_profit=_opt_float(row.get("take_profit")),
                entry_timestamp=str(row.get("entry_timestamp") or ""),
                order_id=str(row.get("order_id") or ""),
                quote_currency=str(row.get("quote_currency") or self.base_currency),
                base_currency=self.base_currency,
                entry_fx=float(row.get("entry_fx") or 1.0),
                current_fx=float(row.get("current_fx") or row.get("entry_fx") or 1.0),
                entry_cost_base=float(row.get("entry_cost_base") or 0.0),
            )
            restored += 1
        equity = self.equity()
        self.peak_equity = max(self.peak_equity, equity)
        self.day_start_equity = equity
        return restored

    # -- FX ---------------------------------------------------------------
    def set_fx(self, quote_currency: str, base_per_quote: float) -> None:
        """Record the rate used to value a quote currency in base terms."""
        code = normalise_currency(quote_currency)
        rate = float(base_per_quote)
        if rate <= 0:
            raise LedgerCurrencyError(f"FX rate for {code} must be positive.")
        self._fx[code] = rate

    def fx_for(self, quote_currency: str) -> float:
        code = normalise_currency(quote_currency)
        if code == self.base_currency:
            return 1.0
        rate = self._fx.get(code)
        if rate is None:
            raise LedgerCurrencyError(
                f"No FX rate for {code}->{self.base_currency}. "
                "The ledger refuses to value a foreign position without one."
            )
        return rate

    def to_base(self, amount_quote: float, quote_currency: str) -> float:
        return money(float(amount_quote) * self.fx_for(quote_currency))

    # -- identifiers ------------------------------------------------------
    def next_order_id(self) -> str:
        self._order_seq += 1
        return f"PAP-{self._order_seq:04d}"

    def next_fill_id(self) -> str:
        self._fill_seq += 1
        return f"FIL-{self._fill_seq:04d}"

    # -- views ------------------------------------------------------------
    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self.positions.values() if p.open]

    @property
    def open_trades(self) -> int:
        return len(self.open_positions())

    def invested_value(self) -> float:
        return money(sum(p.position_value for p in self.open_positions()))

    def unrealised_pnl(self) -> float:
        return money(sum(p.unrealised_pnl for p in self.open_positions()))

    def equity(self) -> float:
        return money(self.cash + self.invested_value())

    def daily_pnl(self) -> float:
        return money(self.equity() - self.day_start_equity)

    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return round(max(0.0, (self.peak_equity - self.equity()) / self.peak_equity), 6)

    def roll_day(self, timestamp: str) -> None:
        key = timestamp[:10]
        if not self.day_key:
            self.day_key = key
            self.day_start_equity = self.equity()
            return
        if key != self.day_key:
            self.day_key = key
            self.day_start_equity = self.equity()
            self.trades_today = 0
            self.entries_today = 0
            self.halted = False

    def mark(self, symbol: str, price: float, *, fx: Optional[float] = None) -> None:
        pos = self.positions.get(symbol)
        if pos and pos.open:
            pos.current_price = price
            pos.current_fx = fx if fx is not None else self.fx_for(pos.quote_currency)
        eq = self.equity()
        if eq > self.peak_equity:
            self.peak_equity = eq

    # -- mutations --------------------------------------------------------
    def apply_buy(
        self,
        order: PaperOrder,
        fill: PaperFill,
        *,
        quote_currency: str = BASE_CURRENCY,
    ) -> None:
        code = normalise_currency(quote_currency)
        fx = self.fx_for(code)
        cost_quote = fill.quantity * fill.price
        cost_base = money(cost_quote * fx)
        if cost_base > self.cash + 0.001:
            raise ValueError("Insufficient cash for paper fill.")
        self.cash = money(self.cash - cost_base)
        self.positions[fill.symbol] = PaperPosition(
            symbol=fill.symbol,
            quantity=fill.quantity,
            average_entry=fill.price,
            current_price=fill.price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            entry_timestamp=fill.timestamp,
            order_id=order.order_id,
            quote_currency=code,
            base_currency=self.base_currency,
            entry_fx=fx,
            current_fx=fx,
            entry_cost_base=cost_base,
            decision_id=order.decision_id,
        )
        self.fills.append(fill)
        self.entries_today += 1
        order.status = "FILLED"
        order.filled_price = fill.price
        order.filled_at = fill.timestamp

    def close_position(
        self,
        symbol: str,
        fill: PaperFill,
        *,
        reason: str,
    ) -> PaperPosition:
        pos = self.positions.get(symbol)
        if not pos or not pos.open:
            raise ValueError("No open position to close.")
        fx = self.fx_for(pos.quote_currency)
        proceeds_base = money(fill.quantity * fill.price * fx)
        pnl = money(proceeds_base - pos.entry_cost_base)
        self.cash = money(self.cash + proceeds_base)
        self.realised_pnl = money(self.realised_pnl + pnl)
        pos.open = False
        pos.current_price = fill.price
        pos.current_fx = fx
        pos.exit_timestamp = fill.timestamp
        pos.realised_pnl = pnl
        pos.quantity = fill.quantity
        self.closed_positions.append(pos)
        del self.positions[symbol]
        self.fills.append(fill)
        # One completed round trip = one trade.
        self.trades_today += 1
        self.round_trips += 1
        return pos

    # -- snapshot ---------------------------------------------------------
    def snapshot(self, as_of: Optional[str] = None) -> PaperAccountState:
        as_of = as_of or utc_now_iso()
        invested = self.invested_value()
        unreal = self.unrealised_pnl()
        total = money(self.realised_pnl + unreal)
        eq = money(self.cash + invested)
        return PaperAccountState(
            currency=self.base_currency,
            starting_cash=self.starting_cash,
            cash=self.cash,
            buying_power=self.cash,
            account_equity=eq,
            invested_value=invested,
            realised_pnl=self.realised_pnl,
            unrealised_pnl=unreal,
            total_pnl=total,
            positions=tuple(p.to_dict() for p in self.open_positions()),
            fill_count=len(self.fills),
            source=SOURCE,
            as_of=as_of,
            drawdown=self.drawdown(),
            daily_pnl=self.daily_pnl(),
            peak_equity=self.peak_equity,
            halted=self.halted,
        )
