"""Deterministic placeholder feed. No network. No live prices."""

from __future__ import annotations

from ai_trader.types import MarketBar, MarketSnapshot, utc_now_iso


class SimulatedMarketData:
    name = "simulated"

    def snapshot(self, symbols: list[str]) -> MarketSnapshot:
        bars = tuple(
            MarketBar(
                symbol=symbol.upper(),
                timestamp=utc_now_iso(),
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                volume=0.0,
            )
            for symbol in symbols
        )
        return MarketSnapshot(
            as_of=utc_now_iso(),
            bars=bars,
            source=self.name,
            notes="Placeholder bars. Market data is not connected yet.",
        )

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": True,
            "notes": "Local placeholder. No external market data.",
        }
