"""Offline fixture AI.

Always returns HOLD. Never talks to the network. Never forecasts. Never trades.
The real Grok adapter will implement the same Analyst interface later.
"""

from __future__ import annotations

import json
from typing import Optional

from ai_trader.ai.base import Analyst, ProposedDecision
from ai_trader.types import Action, Decision, MarketAnalysis, MarketSnapshot, utc_now_iso

MODEL = "fixture-hold"
FIXTURE_CONFIDENCE = 1.0
FIXTURE_REASON = (
    "Fixture Grok adapter — offline, no network. Always HOLD. "
    "This is not a forecast and not a trade."
)


def analysis_ref(analysis: Optional[MarketAnalysis], snapshot: MarketSnapshot) -> str:
    if analysis:
        return f"{analysis.symbol}:{analysis.as_of}"
    if snapshot.bars:
        return f"{snapshot.bars[0].symbol}:{snapshot.as_of}"
    return f"SYSTEM:{snapshot.as_of}"


class FixtureAnalyst(Analyst):
    name = "fixture"

    def propose(
        self,
        snapshot: MarketSnapshot,
        analysis: Optional[MarketAnalysis] = None,
        *,
        account: Optional[dict] = None,
        positions: Optional[list] = None,
        candidate: Optional[dict] = None,
    ) -> ProposedDecision:
        symbol = analysis.symbol if analysis else (
            snapshot.bars[0].symbol if snapshot.bars else "SYSTEM"
        )
        ref = analysis_ref(analysis, snapshot)
        extra = ""
        if analysis:
            extra = (
                f" Analysis {ref}: trend {analysis.trend}, "
                f"price {analysis.current_price}."
            )
        rationale = FIXTURE_REASON + extra
        decision = Decision(
            symbol=symbol,
            action=Action.HOLD,
            confidence=FIXTURE_CONFIDENCE,
            rationale=rationale,
            model=MODEL,
            raw_response="HOLD",
            market_snapshot_json=json.dumps(
                {
                    "analysis_ref": ref,
                    "network": False,
                    "analysis": analysis.to_dict() if analysis else None,
                }
            ),
            analysis_ref=ref,
            created_at=utc_now_iso(),
        )
        return ProposedDecision(
            decision=decision,
            context={"fixture": True, "network": False, "analysis_ref": ref},
        )

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "configured": True,
            "enabled": True,
            "model": MODEL,
            "network": False,
            "notes": "Offline HOLD fixture. No network. Real Grok remains gated.",
        }
