"""AI analysis interface.

The AI may only propose a Decision. It never talks to a broker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from ai_trader.types import Action, Decision, MarketSnapshot


@dataclass(frozen=True)
class ProposedDecision:
    decision: Decision
    context: dict[str, Any]


class Analyst(ABC):
    name: str = "base"

    @abstractmethod
    def propose(
        self, snapshot: MarketSnapshot, analysis: Optional[dict[str, Any]] = None
    ) -> ProposedDecision:
        """Return a BUY / SELL / HOLD proposal. Must not execute anything."""

    def health(self) -> dict:
        return {"name": self.name, "ready": False, "configured": False}


def hold_decision(reason: str, model: str = "none") -> Decision:
    return Decision(
        symbol="SYSTEM",
        action=Action.HOLD,
        confidence=None,
        rationale=reason,
        model=model,
    )
