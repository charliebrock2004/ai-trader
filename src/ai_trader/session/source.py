"""Repeated Grok (or fixture) decisions. Visible candles only. No look-ahead."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ai_trader.ai.base import Analyst
from ai_trader.benchmark.strategies import SimpleTechnicalSource
from ai_trader.paper.models import PaperAction
from ai_trader.types import CandleSeries, MarketAnalysis, MarketSnapshot


def _visible_only(index: int, series: CandleSeries) -> None:
    if len(series.candles) != index + 1:
        raise RuntimeError("Look-ahead: Grok session saw the wrong number of bars.")


def _action_name(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


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


class DeterministicFirstSource:
    """Cheap SMA filter first. Grok only on a surviving candidate, under budget.

    The paper desk must keep running if Grok is missing, disconnected, or over
    budget. Those paths use the deterministic action and never label it as Grok.
    A failed Grok HTTP call is HOLD — we do not invent an analyst decision.
    """

    name = "deterministic-first"

    def __init__(
        self,
        analyst: Optional[Analyst] = None,
        budget: Any = None,
        *,
        technical: Any = None,
        warmup: int = 8,
        account_fn: Optional[Callable[[], dict[str, Any]]] = None,
        stop_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.analyst = analyst
        self.budget = budget
        self.technical = technical or SimpleTechnicalSource()
        self.warmup = max(0, int(warmup))
        self.account_fn = account_fn or (lambda: {})
        self.stop_fn = stop_fn or (lambda: False)
        self.decisions: list[dict[str, Any]] = []
        self.consults = 0
        self.filter_holds = 0
        self.budget_skips = 0
        self.http_skipped_hold = 0

    def _grok_usable(self) -> bool:
        analyst = self.analyst
        if analyst is None:
            return False
        if getattr(analyst, "name", "") == "fixture":
            return False
        configured = getattr(analyst, "is_configured", None)
        if callable(configured) and not configured():
            return False
        if getattr(analyst, "paper_requested", True) is False:
            return False
        return True

    def decide(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
    ) -> PaperAction:
        _visible_only(index, series)
        if self.stop_fn():
            return PaperAction.HOLD
        if index < self.warmup:
            self.http_skipped_hold += 1
            return PaperAction.HOLD

        det = self.technical.decide(index, series, analysis)
        det_name = _action_name(det)
        if det == PaperAction.HOLD or det_name == "HOLD":
            self.filter_holds += 1
            if self.budget is not None:
                self.budget.filter_holds += 1
            return PaperAction.HOLD

        if not self._grok_usable():
            self._record(
                index,
                series,
                analysis,
                action=det_name,
                reasoning="Deterministic SMA 10/20 opportunity. Grok not connected; using the filter only.",
                model="sma-10-20",
                source="deterministic",
            )
            return det

        if self.budget is not None:
            allowed, reason = self.budget.allow()
            if not allowed:
                self.budget_skips += 1
                self._record(
                    index,
                    series,
                    analysis,
                    action=det_name,
                    reasoning=reason,
                    model="sma-10-20",
                    source="deterministic-budget",
                    failure="budget",
                )
                return det

        account = self.account_fn() or {}
        snapshot = MarketSnapshot(
            as_of=analysis.as_of if analysis else series.candles[-1].timestamp,
            bars=tuple(),
            source=series.source or "simulated",
            timeframe=series.timeframe,
            series=(series,),
        )
        proposed = self.analyst.propose(  # type: ignore[union-attr]
            snapshot,
            analysis,
            account=account,
            positions=list(account.get("positions") or []),
        )
        self.consults += 1
        grok_name = _action_name(proposed.decision.action)
        failure = proposed.context.get("failure")
        if failure and failure not in {"none", None}:
            # API down / invalid JSON / timeout: HOLD. Never invent a Grok fill.
            self._record(
                index,
                series,
                analysis,
                action="HOLD",
                reasoning=proposed.decision.rationale,
                model=proposed.decision.model,
                source="grok-failure",
                failure=failure,
                validated=False,
                network=bool(proposed.context.get("network")),
            )
            return PaperAction.HOLD
        if grok_name == "HOLD":
            self._record(
                index,
                series,
                analysis,
                action="HOLD",
                reasoning=proposed.decision.rationale or "Grok challenged the deterministic candidate. HOLD.",
                model=proposed.decision.model,
                source="grok-challenge",
                validated=bool(proposed.context.get("validated")),
                network=True,
            )
            return PaperAction.HOLD
        if grok_name != det_name:
            self._record(
                index,
                series,
                analysis,
                action="HOLD",
                reasoning=(
                    f"Systems disagree: SMA wanted {det_name}, Grok wanted {grok_name}. HOLD."
                ),
                model=proposed.decision.model,
                source="disagree",
                validated=bool(proposed.context.get("validated")),
                network=True,
            )
            return PaperAction.HOLD
        self._record(
            index,
            series,
            analysis,
            action=grok_name,
            reasoning=proposed.decision.rationale,
            model=proposed.decision.model,
            source="grok-agreed",
            validated=bool(proposed.context.get("validated")),
            network=True,
            confidence=proposed.decision.confidence,
        )
        return det

    def _record(
        self,
        index: int,
        series: CandleSeries,
        analysis: Optional[MarketAnalysis],
        *,
        action: str,
        reasoning: str,
        model: str,
        source: str,
        failure: Optional[str] = None,
        validated: bool = False,
        network: bool = False,
        confidence: Any = None,
    ) -> None:
        analysis_bars = getattr(analysis, "bar_count", None) if analysis is not None else None
        self.decisions.append(
            {
                "bar": index,
                "bar_count": len(series.candles),
                "analysis_bar_count": analysis_bars,
                "action": action,
                "confidence": confidence,
                "reasoning": reasoning,
                "model": model,
                "source": source,
                "validated": validated,
                "fixture": False,
                "network": network,
                "failure": failure,
                "timestamp": series.candles[index].timestamp,
            }
        )
