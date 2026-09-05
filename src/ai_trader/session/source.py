"""Repeated Grok (or fixture) decisions. Visible candles only. No look-ahead."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ai_trader.ai.base import Analyst
from ai_trader.paper.models import PaperAction
from ai_trader.strategy.signal import TrendPullbackStrategy
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
    """Cheap deterministic filter first. Grok only on a surviving candidate.

    The paper desk must keep running if Grok is missing, disconnected, or over
    budget. Those paths use the deterministic action and never label it as Grok.
    A failed Grok HTTP call is HOLD — we do not invent an analyst decision.

    The detector is :class:`~ai_trader.strategy.signal.TrendPullbackStrategy`,
    not the frozen benchmark crossover. Those two must stay different: the
    benchmark is the yardstick the live desk is measured against, and a
    yardstick that gets adjusted whenever the desk is quiet measures nothing.
    """

    name = "deterministic-first"

    def __init__(
        self,
        analyst: Optional[Analyst] = None,
        budget: Any = None,
        *,
        technical: Any = None,
        warmup: int = 8,
        timeframe: str = "5m",
        score_threshold: Optional[float] = None,
        account_fn: Optional[Callable[[], dict[str, Any]]] = None,
        stop_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.analyst = analyst
        self.budget = budget
        if technical is None:
            from ai_trader.strategy.signal import SignalConfig

            overrides = (
                {"strong_score": float(score_threshold)}
                if score_threshold is not None
                else {}
            )
            technical = TrendPullbackStrategy(
                SignalConfig.for_timeframe(timeframe, **overrides), timeframe=timeframe
            )
        self.technical = technical
        self.warmup = max(0, int(warmup))
        self.account_fn = account_fn or (lambda: {})
        self.stop_fn = stop_fn or (lambda: False)
        self.decisions: list[dict[str, Any]] = []
        self.consults = 0
        self.filter_holds = 0
        self.budget_skips = 0
        self.http_skipped_hold = 0
        self.latest_signal: Any = None

    def _signal_for(self, index: int, series: CandleSeries, *, has_position: bool):
        """Ask the detector for a full signal, not just an action.

        Falls back to the plain ``decide`` protocol so a custom ``technical``
        (the frozen benchmark strategies, in tests) still works here.
        """
        rich = getattr(self.technical, "last_signal", None)
        if callable(rich):
            return rich(index, series, has_position=has_position)
        from ai_trader.strategy.signal import Signal

        action = self.technical.decide(index, series, None)
        return Signal(
            action=action if isinstance(action, PaperAction) else PaperAction(str(action)),
            rejection=None if _action_name(action) != "HOLD" else "no_signal",
            reason="No trade signal.",
        )

    def signal_summary(self) -> dict[str, Any]:
        """What the detector saw, so the desk can explain its own silence."""
        summary = getattr(self.technical, "summary", None)
        base: dict[str, Any] = summary() if callable(summary) else {}
        base.update(
            {
                "grok_calls": self.consults,
                "filter_holds": self.filter_holds,
                "budget_skips": self.budget_skips,
                "warmup_holds": self.http_skipped_hold,
            }
        )
        return base

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
            # Name the warm-up window in the audit trail. These bars are a HOLD
            # with a real reason, and leaving latest_signal untouched filed them
            # under no reason at all — a generic bucket in the very funnel that
            # exists to explain where opportunities go.
            from ai_trader.strategy.signal import REJECTIONS, Signal

            self.latest_signal = Signal(
                action=PaperAction.HOLD,
                rejection="session_warmup",
                reason=REJECTIONS["session_warmup"],
            )
            return PaperAction.HOLD

        account = self.account_fn() or {}
        has_position = any(
            str(pos.get("symbol")) == series.symbol
            for pos in (account.get("positions") or [])
            if isinstance(pos, dict)
        )

        signal = self._signal_for(index, series, has_position=has_position)
        self.latest_signal = signal
        det = signal.action
        det_name = _action_name(det)
        if det == PaperAction.HOLD or det_name == "HOLD":
            self.filter_holds += 1
            if self.budget is not None:
                self.budget.filter_holds += 1
            # Record *why*. A silent counter cannot distinguish "the market
            # offered nothing" from "the detector is broken", which is the
            # ambiguity that let a desk sit idle for days without anyone being
            # able to tell which had happened.
            self._record(
                index,
                series,
                analysis,
                action="HOLD",
                reasoning=signal.reason,
                model=getattr(self.technical, "name", "deterministic"),
                source="filter",
                rejection=signal.rejection,
                features=signal.features,
            )
            return PaperAction.HOLD

        if not self._grok_usable():
            self._record(
                index,
                series,
                analysis,
                action=det_name,
                reasoning=(
                    f"Deterministic candidate: {signal.reason} "
                    "Grok not connected; using the detector only."
                ),
                model=getattr(self.technical, "name", "deterministic"),
                features=signal.features,
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
                    model=getattr(self.technical, "name", "deterministic"),
                    source="deterministic-budget",
                    features=signal.features,
                    failure="budget",
                )
                return det

        snapshot = MarketSnapshot(
            as_of=analysis.as_of if analysis else series.candles[-1].timestamp,
            bars=tuple(),
            source=series.source or "simulated",
            timeframe=series.timeframe,
            series=(series,),
        )
        # Grok is being asked to challenge a specific, already-priced candidate,
        # so it gets the candidate: the direction, the indicators that produced
        # it, and what the trade costs. Asking "what do you think of BTC" and
        # acting on the answer would be a different and much worse system.
        candidate = {
            "direction": det_name,
            "detector": getattr(self.technical, "name", "deterministic"),
            "reason": signal.reason,
            "features": dict(signal.features),
        }
        try:
            proposed = self.analyst.propose(  # type: ignore[union-attr]
                snapshot,
                analysis,
                account=account,
                positions=list(account.get("positions") or []),
                candidate=candidate,
            )
        except Exception as exc:  # noqa: BLE001
            # An analyst that raises must not take the desk down with it. The
            # client turns transport failures into a HOLD context, but a bug or
            # an unexpected error type would otherwise propagate out of the
            # session thread and stop the worker trading at all. Any exception
            # is the same answer as any other failure: HOLD, recorded.
            self.consults += 1
            self._record(
                index,
                series,
                analysis,
                action="HOLD",
                reasoning=f"Analyst call failed ({type(exc).__name__}). Holding.",
                model=getattr(self.analyst, "name", "analyst"),
                source="grok-failure",
                failure="exception",
                validated=False,
                network=True,
                features=signal.features,
            )
            return PaperAction.HOLD
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
        rejection: Optional[str] = None,
        features: Optional[dict[str, Any]] = None,
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
                #: The named reason the desk declined, when it declined.
                "rejection": rejection,
                "features": dict(features or {}),
                "timestamp": series.candles[index].timestamp,
            }
        )
