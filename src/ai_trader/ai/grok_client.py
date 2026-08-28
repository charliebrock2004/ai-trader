"""Grok / xAI analysis adapter.

Foundation rules:
- No automatic calls.
- No network on import.
- propose() refuses until this layer is explicitly enabled in a later step.
- Even once enabled, output is a Decision proposal only — never an order.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.ai.base import Analyst, ProposedDecision
from ai_trader.config import Settings
from ai_trader.exceptions import FoundationModeError
from ai_trader.types import MarketSnapshot

XAI_CHAT_PATH = "/chat/completions"
DEFAULT_MODEL = "grok-4.5"


class GrokAnalyst(Analyst):
    name = "grok"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = False  # flipped in a later build, never by env alone

    def is_configured(self) -> bool:
        return self.settings.grok_configured()

    def propose(
        self, snapshot: MarketSnapshot, analysis: Optional[dict[str, Any]] = None
    ) -> ProposedDecision:
        if not self.enabled:
            raise FoundationModeError(
                "Grok analysis is stubbed in the foundation build. "
                "The client will not call the xAI API yet."
            )
        raise FoundationModeError("Grok analysis is not enabled.")

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": False,
            "configured": self.is_configured(),
            "enabled": self.enabled,
            "model": self.settings.xai_model,
            "base_url": self.settings.xai_base_url,
            "notes": (
                "Will later POST to /chat/completions with model grok-4.5. "
                "Output is a BUY/SELL/HOLD proposal only."
            ),
        }

    def future_request_shape(self) -> dict:
        """Document the intended call. Not sent."""
        return {
            "method": "POST",
            "url": f"{self.settings.xai_base_url}{XAI_CHAT_PATH}",
            "model": self.settings.xai_model or DEFAULT_MODEL,
            "headers": ["Authorization: Bearer <XAI_API_KEY>", "Content-Type: application/json"],
            "body": {
                "model": self.settings.xai_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Propose BUY, SELL, or HOLD. Never execute trades.",
                    },
                    {"role": "user", "content": "<market snapshot + analysis>"},
                ],
            },
            "sent": False,
        }
