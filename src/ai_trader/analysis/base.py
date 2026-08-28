"""Market / news analysis layer. Feeds the AI. Not implemented yet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ai_trader.exceptions import FoundationModeError
from ai_trader.types import MarketSnapshot


class MarketAnalyst(ABC):
    name: str = "base"

    @abstractmethod
    def analyse(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Turn a snapshot into structured context for the AI layer."""

    def health(self) -> dict:
        return {"name": self.name, "ready": False}


class NullAnalyst(MarketAnalyst):
    name = "null"

    def analyse(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        raise FoundationModeError(
            "Market/news analysis is not implemented in the foundation build."
        )

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": False,
            "notes": "Stub. Will later summarise price action and news.",
        }
