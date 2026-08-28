"""In-process simulated broker.

Does not talk to the network. Does not place orders in the foundation build.
"""

from __future__ import annotations

from typing import Any

from ai_trader.broker.base import Broker, assert_may_submit
from ai_trader.exceptions import OrderPlacementDisabledError
from ai_trader.types import IntendedOrder, RiskVerdict


class SimulatedBroker(Broker):
    name = "simulated"

    def __init__(self) -> None:
        self.submitted: list[IntendedOrder] = []

    def submit(self, order: IntendedOrder, verdict: RiskVerdict) -> dict[str, Any]:
        assert_may_submit(verdict)
        raise OrderPlacementDisabledError(
            "Simulated broker will not place orders in the foundation build."
        )

    def account(self) -> dict[str, Any]:
        return {
            "available": True,
            "source": self.name,
            "cash": None,
            "equity": None,
            "note": "No simulated balances yet.",
        }

    def health(self) -> dict:
        return {
            "name": self.name,
            "connected": True,
            "orders_enabled": False,
            "notes": "Local stub. No orders are sent anywhere.",
        }
