"""Paper account interface.

Read-only for analysis and risk. Implementations must stay simulated.
There is no method here that can attach a live broker account.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_trader.types import PaperAccountState


class PaperAccount(ABC):
    name: str = "base"

    @abstractmethod
    def snapshot(self) -> PaperAccountState:
        """Current balances and positions. Must not place orders."""

    def health(self) -> dict:
        return {"name": self.name, "ready": False, "live": False}
