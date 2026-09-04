"""Grok is optional, rate-limited, and never a reason to trade."""

from __future__ import annotations

from ai_trader.ai.base import ProposedDecision
from ai_trader.ai.budget import GrokBudget
from ai_trader.clock import FrozenClock
from ai_trader.db.records import RecordStore
from ai_trader.db.schema import initialise_database
from ai_trader.market_data.generator import generate_series
from ai_trader.paper.models import PaperAction
from ai_trader.session.source import DeterministicFirstSource
from ai_trader.types import Action, Decision, utc_now_iso


class _Always:
    def __init__(self, action: PaperAction) -> None:
        self.action = action

    def decide(self, index, series, analysis):
        return self.action


class _FakeAnalyst:
    name = "grok"

    def __init__(self, action: Action = Action.BUY, *, failure: str | None = None) -> None:
        self.calls = 0
        self.paper_requested = True
        self.action = action
        self.failure = failure

    def is_configured(self) -> bool:
        return True

    def propose(self, snapshot, analysis=None, **kwargs):
        self.calls += 1
        return ProposedDecision(
            decision=Decision(
                symbol="TEST",
                action=Action.HOLD if self.failure else self.action,
                confidence=None if self.failure else 0.5,
                rationale="timeout" if self.failure else "ok",
                model="grok-4.3",
                created_at=utc_now_iso(),
            ),
            context={
                "fixture": False,
                "network": True,
                "validated": self.failure is None,
                "failure": self.failure,
            },
        )


def _visible(series, index):
    from dataclasses import replace

    return replace(series, candles=series.candles[: index + 1])


def _budget(tmp_path, *, daily=8, interval=1800, clock=None) -> GrokBudget:
    clock = clock or FrozenClock("2026-09-04T12:00:00+00:00")
    store = RecordStore(initialise_database(tmp_path / "a.db"), clock=clock)
    return GrokBudget(store, clock=clock, daily_limit=daily, min_interval_seconds=interval)


def test_daily_budget_blocks_the_ninth_call(tmp_path) -> None:
    clock = FrozenClock("2026-09-04T12:00:00+00:00")
    budget = _budget(tmp_path, daily=8, interval=0, clock=clock)
    for _ in range(8):
        ok, _reason = budget.consume()
        assert ok
    ok, reason = budget.consume()
    assert ok is False
    assert "exhausted" in reason.lower()
    assert budget.calls_today() == 8


def test_min_interval_blocks_a_second_call(tmp_path) -> None:
    clock = FrozenClock("2026-09-04T12:00:00+00:00")
    budget = _budget(tmp_path, daily=8, interval=1800, clock=clock)
    assert budget.consume()[0] is True
    assert budget.consume()[0] is False
    clock.advance(1800)
    assert budget.consume()[0] is True


def test_filter_hold_does_not_call_grok() -> None:
    series = generate_series("SIM-FLAT", limit=12, seed=42)
    analyst = _FakeAnalyst()
    source = DeterministicFirstSource(analyst, technical=_Always(PaperAction.HOLD), warmup=0)
    visible = _visible(series, 8)
    action = source.decide(8, visible, None)
    assert action == PaperAction.HOLD
    assert analyst.calls == 0
    assert source.filter_holds == 1


def test_operates_without_grok_using_the_filter() -> None:
    series = generate_series("SIM-UP", limit=12, seed=42)
    source = DeterministicFirstSource(
        analyst=None, technical=_Always(PaperAction.BUY), warmup=0
    )
    visible = _visible(series, 8)
    action = source.decide(8, visible, None)
    assert action == PaperAction.BUY
    assert source.consults == 0
    assert source.decisions[0]["model"] == "sma-10-20"
    assert source.decisions[0]["source"] == "deterministic"


def test_failed_grok_call_is_hold_not_a_fabricated_buy() -> None:
    series = generate_series("SIM-UP", limit=12, seed=42)
    analyst = _FakeAnalyst(Action.BUY, failure="timeout")
    source = DeterministicFirstSource(
        analyst, technical=_Always(PaperAction.BUY), warmup=0
    )
    visible = _visible(series, 8)
    action = source.decide(8, visible, None)
    assert action == PaperAction.HOLD
    assert analyst.calls == 1
    assert source.decisions[0]["action"] == "HOLD"
    assert source.decisions[0]["source"] == "grok-failure"


def test_budget_exhausted_continues_on_the_filter(tmp_path) -> None:
    series = generate_series("SIM-UP", limit=12, seed=42)
    clock = FrozenClock("2026-09-04T12:00:00+00:00")
    budget = _budget(tmp_path, daily=1, interval=0, clock=clock)
    assert budget.consume()[0] is True
    analyst = _FakeAnalyst()
    source = DeterministicFirstSource(
        analyst, budget, technical=_Always(PaperAction.BUY), warmup=0
    )
    visible = _visible(series, 8)
    action = source.decide(8, visible, None)
    assert action == PaperAction.BUY
    assert analyst.calls == 0
    assert source.decisions[0]["source"] == "deterministic-budget"


def test_grok_agreement_is_the_only_path_that_keeps_the_candidate() -> None:
    series = generate_series("SIM-UP", limit=12, seed=42)
    analyst = _FakeAnalyst(Action.BUY)
    source = DeterministicFirstSource(
        analyst, technical=_Always(PaperAction.BUY), warmup=0
    )
    visible = _visible(series, 8)
    action = source.decide(8, visible, None)
    assert action == PaperAction.BUY
    assert analyst.calls == 1
    assert source.decisions[0]["source"] == "grok-agreed"
