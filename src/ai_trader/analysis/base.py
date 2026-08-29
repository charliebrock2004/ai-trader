"""Market / news analysis layer. Feeds the AI. Never executes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ai_trader.exceptions import FoundationModeError
from ai_trader.types import AnalysisBundle, MarketSnapshot, utc_now_iso, utc_now_iso


class MarketAnalyst(ABC):
    name: str = "base"

    @abstractmethod
    def analyse(self, snapshot: MarketSnapshot) -> AnalysisBundle:
        """Turn a snapshot into structured context for the AI layer."""

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "ready": False}


class NullAnalyst(MarketAnalyst):
    name = "null"

    def analyse(self, snapshot: MarketSnapshot) -> AnalysisBundle:
        raise FoundationModeError(
            "Null analyst is a placeholder. Use TechnicalAnalyst for read-only math."
        )

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": False,
            "notes": "Placeholder. TechnicalAnalyst is the read-only implementation.",
        }
