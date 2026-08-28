"""Market data interface. Swap this later for Alpaca data, Polygon, etc."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_trader.types import MarketSnapshot


class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def snapshot(self, symbols: list[str]) -> MarketSnapshot:
        """Return the latest bars for the requested symbols."""

    def health(self) -> dict:
        return {"name": self.name, "ready": False, "notes": "Interface only."}
