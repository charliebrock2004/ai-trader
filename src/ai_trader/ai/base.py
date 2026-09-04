"""AI analysis interface.

The AI may only propose a Decision. It never talks to a broker.
FixtureAnalyst and the future GrokAnalyst both implement Analyst.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from ai_trader.types import Action, Decision, MarketAnalysis, MarketSnapshot


@dataclass(frozen=True)
class ProposedDecision:
    decision: Decision
    context: dict[str, Any]


class Analyst(ABC):
    """Swap-in protocol. Implementations must not place orders or open sockets
    unless a later, explicit build enables the real Grok client.
    """

    name: str = "base"

    @abstractmethod
    def propose(
        self,
        snapshot: MarketSnapshot,
        analysis: Optional[MarketAnalysis] = None,
        *,
        account: Optional[dict[str, Any]] = None,
        positions: Optional[list] = None,
        candidate: Optional[dict[str, Any]] = None,
    ) -> ProposedDecision:
        """Return a BUY / SELL / HOLD proposal. Must not execute anything.

        ``candidate`` is the deterministic proposal being challenged: its
        direction, the indicators behind it and what it costs. An analyst that
        ignores it is answering a vaguer question than the desk is asking.
        """

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
