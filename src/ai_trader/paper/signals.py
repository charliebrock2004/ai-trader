"""Signal sources for the paper simulator.

Not a trading strategy. Scripted sources exist so the engine can be tested
without Grok. FixtureHoldSource is the default dry-cycle behaviour.
"""

from __future__ import annotations

from typing import Optional, Protocol

from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis


class SignalSource(Protocol):
    name: str

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        ...


class FixtureHoldSource:
    name = "fixture-hold"

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        return PaperAction.HOLD


class ScriptedSignalSource:
    """bar_index → action. Index is the signal bar (close). No look-ahead."""

    name = "scripted"

    def __init__(self, plan: dict[int, PaperAction], *, name: str = "scripted") -> None:
        self.plan = dict(plan)
        self.name = name

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        return self.plan.get(index, PaperAction.HOLD)


# Demo-only. Not Grok. Not a strategy. Buys once on SIM-UP bar 20.
DEMO_SIM_UP = ScriptedSignalSource({20: PaperAction.BUY}, name="demo-sim-up")



class FrozenActionSource:
    """Replay a single already-validated decision at one bar. No look-ahead."""

    def __init__(self, action: PaperAction, *, index: int, name: str = "grok-once") -> None:
        self.action = action
        self.index = index
        self.name = name

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        if index == self.index:
            return self.action
        return PaperAction.HOLD
