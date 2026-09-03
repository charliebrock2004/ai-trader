"""Binary-contract ledger.

Kept separate from ``PaperLedger`` on purpose. A spot position has an average
entry, a mark, a stop and an unrealised P&L that moves continuously. A binary
position has a premium, a fixed maximum loss, a fixed maximum payout, and no
stop at all — it either settles at 1 or at 0. Bending one ledger into both
shapes is how accounting bugs get written.

    premium   = contracts x price          (paid up front, in quote currency)
    max loss  = premium + fees             (the whole thing, if it resolves NO)
    max payout= contracts x 1
    max profit= contracts x (1 - price) - fees

Cash, exposure and P&L are held in the account's **base** currency. Contract
prices are in the venue's quote currency, converted with an explicit rate at
entry and at settlement, exactly as the spot ledger does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ai_trader.money import BASE_CURRENCY, money_float, normalise_currency


class ContractLedgerError(RuntimeError):
    """Refused: bad currency, insufficient cash, or an impossible position."""


@dataclass
class ContractPosition:
    position_id: str
    ticker: str
    event_key: str
    side: str
    contracts: int
    average_price: float
    premium_base: float
    fees_base: float
    entry_fx: float
    quote_currency: str
    opened_at: str
    decision_id: Optional[int] = None
    open: bool = True
    resolved_outcome: Optional[int] = None
    settlement_base: Optional[float] = None
    realised_pnl_base: Optional[float] = None
    closed_at: Optional[str] = None

    @property
    def max_loss_base(self) -> float:
        """Everything paid. A binary has no stop; this is the real risk."""
        return money_float(self.premium_base + self.fees_base)

    @property
    def max_payout_base(self) -> float:
        return money_float(self.contracts * 1.0 * self.entry_fx)

    @property
    def max_gain_base(self) -> float:
        return money_float(self.max_payout_base - self.premium_base - self.fees_base)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "ticker": self.ticker,
            "event_key": self.event_key,
            "side": self.side,
            "contracts": self.contracts,
            "average_price": self.average_price,
            "premium_base": self.premium_base,
            "fees_base": self.fees_base,
            "max_loss_base": self.max_loss_base,
            "max_payout_base": self.max_payout_base,
            "max_gain_base": self.max_gain_base,
            "entry_fx": self.entry_fx,
            "quote_currency": self.quote_currency,
            "opened_at": self.opened_at,
            "open": self.open,
            "resolved_outcome": self.resolved_outcome,
            "settlement_base": self.settlement_base,
            "realised_pnl_base": self.realised_pnl_base,
            "closed_at": self.closed_at,
            "decision_id": self.decision_id,
        }


class ContractLedger:
    """Cash, open contract positions and settlement. Offline. No broker."""

    def __init__(
        self,
        *,
        starting_cash: float = 100.00,
        base_currency: str = BASE_CURRENCY,
    ) -> None:
        cash = money_float(starting_cash)
        self.base_currency = normalise_currency(base_currency)
        self.starting_cash = cash
        self.cash = cash
        self.realised_pnl = 0.0
        self.fees_paid = 0.0
        self.peak_equity = cash
        self.day_key = ""
        self.day_start_equity = cash
        self.positions_opened_today = 0
        self.settled_today = 0
        self.positions: dict[str, ContractPosition] = {}
        self.closed: list[ContractPosition] = []
        self._fx: dict[str, float] = {self.base_currency: 1.0}
        self._seq = 0

    # -- FX ---------------------------------------------------------------
    def set_fx(self, quote_currency: str, base_per_quote: float) -> None:
        code = normalise_currency(quote_currency)
        if float(base_per_quote) <= 0:
            raise ContractLedgerError(f"FX rate for {code} must be positive.")
        self._fx[code] = float(base_per_quote)

    def fx_for(self, quote_currency: str) -> float:
        code = normalise_currency(quote_currency)
        if code == self.base_currency:
            return 1.0
        rate = self._fx.get(code)
        if rate is None:
            raise ContractLedgerError(
                f"No FX rate for {code}->{self.base_currency}. "
                "The ledger refuses to value a foreign contract without one."
            )
        return rate

    def next_position_id(self) -> str:
        self._seq += 1
        return f"POS-{self._seq:05d}"

    # -- views ------------------------------------------------------------
    def open_positions(self) -> list[ContractPosition]:
        return [p for p in self.positions.values() if p.open]

    def total_exposure(self) -> float:
        """Sum of what could be lost. This is the number risk cares about."""
        return money_float(sum(p.max_loss_base for p in self.open_positions()))

    def exposure_for_event(self, event_key: str) -> float:
        return money_float(
            sum(p.max_loss_base for p in self.open_positions() if p.event_key == event_key)
        )

    def exposure_for_ticker(self, ticker: str) -> float:
        return money_float(
            sum(p.max_loss_base for p in self.open_positions() if p.ticker == ticker)
        )

    def equity(self) -> float:
        """Cash plus premium at cost.

        Open binaries are carried at cost rather than marked to the book. A
        thin prediction-market book makes a mark-to-mid equity figure jump on
        one resting order, and equity drives the survival state — so the
        conservative, stable choice is cost until settlement.
        """
        return money_float(self.cash + sum(p.premium_base for p in self.open_positions()))

    def daily_pnl(self) -> float:
        return money_float(self.equity() - self.day_start_equity)

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
            self.positions_opened_today = 0
            self.settled_today = 0

    # -- mutations --------------------------------------------------------
    def open_position(
        self,
        *,
        ticker: str,
        event_key: str,
        contracts: int,
        price: float,
        fee: float,
        quote_currency: str,
        opened_at: str,
        side: str = "YES",
        decision_id: Optional[int] = None,
        position_id: Optional[str] = None,
    ) -> ContractPosition:
        if contracts <= 0:
            raise ContractLedgerError("Contract count must be positive.")
        if not 0.0 < price < 1.0:
            raise ContractLedgerError(f"Contract price {price} must be strictly inside (0, 1).")
        code = normalise_currency(quote_currency)
        fx = self.fx_for(code)
        premium_base = money_float(contracts * price * fx)
        fee_base = money_float(fee * fx)
        outlay = money_float(premium_base + fee_base)
        if outlay > self.cash + 0.001:
            raise ContractLedgerError(
                f"Insufficient cash: {outlay:.2f} needed, {self.cash:.2f} available."
            )
        if ticker in self.positions and self.positions[ticker].open:
            raise ContractLedgerError(
                f"Already holding {ticker}. Adds are disabled; exposure would compound."
            )
        self.cash = money_float(self.cash - outlay)
        self.fees_paid = money_float(self.fees_paid + fee_base)
        position = ContractPosition(
            position_id=position_id or self.next_position_id(),
            ticker=ticker,
            event_key=event_key,
            side=side,
            contracts=int(contracts),
            average_price=round(float(price), 6),
            premium_base=premium_base,
            fees_base=fee_base,
            entry_fx=fx,
            quote_currency=code,
            opened_at=opened_at,
            decision_id=decision_id,
        )
        self.positions[ticker] = position
        self.positions_opened_today += 1
        self._touch_peak()
        return position

    def settle(
        self,
        ticker: str,
        *,
        outcome: int,
        settled_at: str,
        settlement_fee: float = 0.0,
    ) -> ContractPosition:
        """Resolve a position. ``outcome`` is 1 for YES, 0 for NO.

        A YES position settling YES pays ``contracts x 1``. Settling NO pays
        nothing — the whole premium is gone. That is the shape a stop loss
        cannot protect against, which is why sizing treats premium as the loss.
        """
        position = self.positions.get(ticker)
        if position is None or not position.open:
            raise ContractLedgerError(f"No open position in {ticker} to settle.")
        outcome = 1 if int(outcome) else 0
        fx = self.fx_for(position.quote_currency)
        wins = (position.side == "YES" and outcome == 1) or (
            position.side == "NO" and outcome == 0
        )
        payout_quote = position.contracts * 1.0 if wins else 0.0
        fee_base = money_float(settlement_fee * fx)
        settlement_base = money_float(payout_quote * fx - fee_base)
        self.cash = money_float(self.cash + settlement_base)
        self.fees_paid = money_float(self.fees_paid + fee_base)
        pnl = money_float(settlement_base - position.premium_base - position.fees_base)
        self.realised_pnl = money_float(self.realised_pnl + pnl)
        position.open = False
        position.resolved_outcome = outcome
        position.settlement_base = settlement_base
        position.realised_pnl_base = pnl
        position.closed_at = settled_at
        self.closed.append(position)
        del self.positions[ticker]
        self.settled_today += 1
        self._touch_peak()
        return position

    def _touch_peak(self) -> None:
        eq = self.equity()
        if eq > self.peak_equity:
            self.peak_equity = eq

    # -- snapshot ---------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        open_positions = self.open_positions()
        return {
            "base_currency": self.base_currency,
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "equity": self.equity(),
            "premium_at_risk": money_float(sum(p.premium_base for p in open_positions)),
            "total_exposure": self.total_exposure(),
            "max_payout": money_float(sum(p.max_payout_base for p in open_positions)),
            "realised_pnl": self.realised_pnl,
            "fees_paid": self.fees_paid,
            "peak_equity": self.peak_equity,
            "drawdown": self.drawdown(),
            "daily_pnl": self.daily_pnl(),
            "open_positions": [p.to_dict() for p in open_positions],
            "closed_positions": len(self.closed),
            "positions_opened_today": self.positions_opened_today,
        }
