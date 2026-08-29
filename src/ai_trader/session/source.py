"""Repeated Grok (or fixture) decisions. Visible candles only. No look-ahead."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ai_trader.ai.base import Analyst
from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis, MarketSnapshot


def _visible_only(index: int, series: CandleSeries) -> None:
    if len(series.candles) != index + 1:
        raise RuntimeError("Look-ahead: Grok session saw the wrong number of bars.")


class RepeatingGrokSource:
    """Consult Grok every N bars. HOLD on other bars. Never sees future candles."""

    name = "grok-session"

    def __init__(
        self,
        analyst: Analyst,
        *,
        frequency: int = 8,
        warmup: int = 8,
        account_fn: Optional[Callable[[], dict[str, Any]]] = None,
        stop_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.analyst = analyst
        self.frequency = max(1, int(frequency))
        self.warmup = max(0, int(warmup))
        self.account_fn = account_fn or (lambda: {})
        self.stop_fn = stop_fn or (lambda: False)
        self.decisions: list[dict[str, Any]] = []
        self.consults = 0
        self.http_skipped_hold = 0

    def should_consult(self, index: int) -> bool:
        if index < self.warmup:
            return False
        return (index - self.warmup) % self.frequency == 0

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        if self.stop_fn():
            return PaperAction.HOLD
        if not self.should_consult(index):
            self.http_skipped_hold += 1
            return PaperAction.HOLD
        account = self.account_fn() or {}
        snapshot = MarketSnapshot(
            as_of=analysis.as_of if analysis else series.candles[-1].timestamp,
            bars=tuple(),
            source=series.source or "simulated",
            timeframe=series.timeframe,
            series=(series,),
        )
        proposed = self.analyst.propose(
            snapshot,
            analysis,
            account=account,
            positions=list(account.get("positions") or []),
        )
        self.consults += 1
        decision = proposed.decision
        analysis_bars = getattr(analysis, "bar_count", None) if analysis is not None else None
        record = {
            "bar": index,
            "bar_count": len(series.candles),
            "analysis_bar_count": analysis_bars,
            "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
            "confidence": decision.confidence,
            "reasoning": decision.rationale,
            "model": decision.model,
            "validated": bool(proposed.context.get("validated")),
            "fixture": bool(proposed.context.get("fixture")),
            "network": bool(proposed.context.get("network")),
            "failure": proposed.context.get("failure"),
            "timestamp": series.candles[index].timestamp,
        }
        self.decisions.append(record)
        action = record["action"]
        if action == "BUY":
            return PaperAction.BUY
        if action == "SELL":
            return PaperAction.SELL
        return PaperAction.HOLD
