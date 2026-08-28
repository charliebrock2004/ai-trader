"""Broker interface.

Implementations must:
- refuse live endpoints
- refuse orders that were not approved by the risk engine
- no-op / raise during the foundation build
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ai_trader.exceptions import OrderPlacementDisabledError
from ai_trader.types import IntendedOrder, RiskVerdict


class Broker(ABC):
    name: str = "base"

    @abstractmethod
    def submit(self, order: IntendedOrder, verdict: RiskVerdict) -> dict[str, Any]:
        """Submit an order. Foundation implementations must not send anything."""

    def account(self) -> dict[str, Any]:
        return {"available": False}

    def positions(self) -> list[dict[str, Any]]:
        return []

    def health(self) -> dict:
        return {"name": self.name, "connected": False, "orders_enabled": False}


def assert_may_submit(verdict: RiskVerdict) -> None:
    if not verdict.approved:
        raise OrderPlacementDisabledError(
            verdict.reason or "Risk engine did not approve this order."
        )
