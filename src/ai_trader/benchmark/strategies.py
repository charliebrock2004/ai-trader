"""Four frozen paper strategies. Do not tune these against simulated results.

BUY_AND_HOLD, SIMPLE_TECHNICAL, and RANDOM_BASELINE never call Grok.
GROK uses the existing Analyst interface (fixture by default; real Grok only
when GROK_PAPER_ANALYSIS is on).
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.account.simulated import STARTING_CASH
from ai_trader.analysis.indicators import sma
from ai_trader.ai.base import Analyst
from ai_trader.market_data.generator import LCG
from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis, MarketSnapshot

# Frozen a priori. Not fitted to SIM-* paths.
SMA_FAST = 10
SMA_SLOW = 20
BUY_AND_HOLD_BAR = 0
GROK_DECISION_BAR = 20  # first bar where SMA20 exists; not a fitted delay
RANDOM_SEED = 7
RANDOM_WARMUP = 10

STRATEGY_NAMES = (
    "BUY_AND_HOLD",
    "SIMPLE_TECHNICAL",
    "RANDOM_BASELINE",
    "GROK",
)


def _visible_only(index: int, series: CandleSeries) -> None:
    if len(series.candles) != index + 1:
        raise RuntimeError("Look-ahead: strategy saw the wrong number of bars.")


class BuyAndHoldSource:
    name = "BUY_AND_HOLD"

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        if index == BUY_AND_HOLD_BAR:
            return PaperAction.BUY
        return PaperAction.HOLD


class SimpleTechnicalSource:
    """Textbook SMA 10/20 cross. Long-only. Not optimised on these series."""

    name = "SIMPLE_TECHNICAL"

    def __init__(self, *, fast: int = SMA_FAST, slow: int = SMA_SLOW) -> None:
        self.fast = fast
        self.slow = slow

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        closes = [c.close for c in series.candles]
        if len(closes) < self.slow + 1:
            return PaperAction.HOLD
        fast_now = sma(closes, self.fast)
        slow_now = sma(closes, self.slow)
        fast_prev = sma(closes[:-1], self.fast)
        slow_prev = sma(closes[:-1], self.slow)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return PaperAction.HOLD
        if fast_prev <= slow_prev and fast_now > slow_now:
            return PaperAction.BUY
        if fast_prev >= slow_prev and fast_now < slow_now:
            return PaperAction.SELL
        return PaperAction.HOLD


class RandomBaselineSource:
    """Seeded uninformed policy. Same seed → same actions. Not AI."""

    name = "RANDOM_BASELINE"

    def __init__(self, *, seed: int = RANDOM_SEED, warmup: int = RANDOM_WARMUP) -> None:
        self.seed = seed
        self.warmup = warmup
        self.rng = LCG(seed)

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        if index < self.warmup:
            return PaperAction.HOLD
        draw = self.rng.next()
        if draw < 1.0 / 3.0:
            return PaperAction.BUY
        if draw < 2.0 / 3.0:
            return PaperAction.SELL
        return PaperAction.HOLD


class GrokOnceSource:
    """One Grok (or fixture) decision per series. Sees candles through decision bar only."""

    name = "GROK"

    def __init__(
        self,
        analyst: Analyst,
        *,
        decision_bar: int = GROK_DECISION_BAR,
        starting_cash: float = STARTING_CASH,
    ) -> None:
        self.analyst = analyst
        self.decision_bar = decision_bar
        self.starting_cash = starting_cash
        self.decisions: list[dict[str, Any]] = []

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        if index != self.decision_bar:
            return PaperAction.HOLD
        snapshot = MarketSnapshot(
            as_of=analysis.as_of if analysis else series.candles[-1].timestamp,
            bars=tuple(),
            source="simulated",
            timeframe=series.timeframe,
            series=(series,),
        )
        account = {
            "currency": "GBP",
            "cash": self.starting_cash,
            "buying_power": self.starting_cash,
            "account_equity": self.starting_cash,
            "invested_value": 0.0,
            "unrealised_pnl": 0.0,
            "realised_pnl": 0.0,
            "halted": False,
            "live": False,
            "positions": [],
        }
        proposed = self.analyst.propose(
            snapshot,
            analysis,
            account=account,
            positions=[],
        )
        decision = proposed.decision
        record = {
            "bar": index,
            "bar_count": len(series.candles),
            "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
            "confidence": decision.confidence,
            "reasoning": decision.rationale,
            "model": decision.model,
            "validated": bool(proposed.context.get("validated")),
            "fixture": bool(proposed.context.get("fixture")),
            "network": bool(proposed.context.get("network")),
        }
        self.decisions.append(record)
        action = record["action"]
        if action == "BUY":
            return PaperAction.BUY
        if action == "SELL":
            return PaperAction.SELL
        return PaperAction.HOLD
