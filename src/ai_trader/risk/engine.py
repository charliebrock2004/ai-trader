"""Risk management engine.

Every AI decision must pass through here before the broker is even considered.
During the foundation build every verdict is a rejection.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.types import Action, Decision, RiskVerdict


FOUNDATION_REJECT_REASON = (
    "Foundation mode: the risk engine rejects all orders. "
    "Execution is not implemented."
)


class RiskEngine:
    name = "risk"

    def __init__(self, *, max_position_pct: float = 0.02, allow_orders: bool = False) -> None:
        self.max_position_pct = max_position_pct
        self.allow_orders = allow_orders  # stays False in this build

    def review(
        self,
        decision: Decision,
        *,
        account: Optional[dict[str, Any]] = None,
        positions: Optional[list[dict[str, Any]]] = None,
    ) -> RiskVerdict:
        if decision.action == Action.HOLD:
            return RiskVerdict(
                approved=False,
                reason="HOLD does not produce an order.",
                max_qty=0,
                decision=decision,
            )
        if not self.allow_orders:
            return RiskVerdict(
                approved=False,
                reason=FOUNDATION_REJECT_REASON,
                max_qty=0,
                decision=decision,
            )
        return RiskVerdict(
            approved=False,
            reason="Risk engine has no live policy yet; default is reject.",
            max_qty=0,
            decision=decision,
        )

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "allow_orders": self.allow_orders,
            "max_position_pct": self.max_position_pct,
            "notes": "Hard gate. AI cannot bypass this module.",
        }
