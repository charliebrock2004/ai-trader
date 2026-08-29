"""Offline simulated paper account.

Starts at £100.00. No fills. No positions. No network. Not convertible to live.
"""

from __future__ import annotations

from ai_trader.account.base import PaperAccount
from ai_trader.types import PaperAccountState, utc_now_iso

STARTING_CASH = 100.00
CURRENCY = "GBP"
SOURCE = "simulated-paper"


def _money(value: float) -> float:
    return round(float(value) + 1e-12, 2)


class SimulatedPaperAccount(PaperAccount):
    name = "simulated-paper"

    def __init__(self, *, starting_cash: float = STARTING_CASH) -> None:
        cash = _money(starting_cash)
        self._starting_cash = cash
        self._cash = cash
        self._fill_count = 0
        self._positions: tuple = ()

    def snapshot(self) -> PaperAccountState:
        invested = 0.00
        realised = 0.00
        unrealised = 0.00
        return PaperAccountState(
            currency=CURRENCY,
            starting_cash=self._starting_cash,
            cash=self._cash,
            buying_power=self._cash,
            account_equity=_money(self._cash + invested),
            invested_value=invested,
            realised_pnl=realised,
            unrealised_pnl=unrealised,
            total_pnl=_money(realised + unrealised),
            positions=self._positions,
            fill_count=self._fill_count,
            source=SOURCE,
            as_of=utc_now_iso(),
        )

    def health(self) -> dict:
        snap = self.snapshot()
        return {
            "name": self.name,
            "ready": True,
            "live": False,
            "currency": snap.currency,
            "cash": snap.cash,
            "fill_count": snap.fill_count,
            "notes": "Offline £100 paper account. Read-only. No fills.",
        }
