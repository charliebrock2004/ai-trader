from __future__ import annotations

import pytest

from ai_trader.ai.fixture import FIXTURE_CONFIDENCE, FixtureAnalyst
from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.analysis.technical import analyse_series
from ai_trader.config import Settings
from ai_trader.db.repository import Repository
from ai_trader.exceptions import OrderPlacementDisabledError
from ai_trader.kill_switch import KillSwitch
from ai_trader.market_data.generator import generate_series
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.types import Action, MarketSnapshot, utc_now_iso


def test_fixture_always_hold() -> None:
    series = generate_series("SIM-UP", limit=60, seed=42)
    analysis = analyse_series(series)
    snapshot = MarketSnapshot(
        as_of=analysis.as_of,
        bars=tuple(),
        source="simulated",
        series=(series,),
    )
    proposed = FixtureAnalyst().propose(snapshot, analysis)
    assert proposed.decision.action == Action.HOLD
    assert proposed.decision.confidence == FIXTURE_CONFIDENCE
    assert proposed.decision.analysis_ref
    assert proposed.context["network"] is False
    assert "offline" in proposed.decision.rationale.lower()
    assert "forecast" in proposed.decision.rationale.lower()


def test_fixture_has_no_network_imports() -> None:
    import ai_trader.ai.fixture as mod

    assert "httpx" not in mod.__dict__
    assert "requests" not in mod.__dict__
    assert "urllib" not in mod.__dict__


def test_real_grok_still_disabled(isolated_env: object) -> None:
    analyst = GrokAnalyst(Settings())
    snapshot = MarketSnapshot(as_of=utc_now_iso(), bars=tuple(), source="test")
    proposed = analyst.propose(snapshot)
    assert proposed.decision.action.value == "HOLD"
    assert analyst.enabled is False
    assert analyst.http_calls == []


def test_dry_run_hold_is_persisted_and_rejected(isolated_env: object) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)

    submit_calls: list[str] = []

    def trap(*args, **kwargs):
        submit_calls.append("called")
        raise AssertionError("broker submit must not be called")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]

    result = orch.dry_run(["SPY"])
    assert result["ok"] is True
    assert result["orders_placed"] == 0
    assert result["risk_approved"] is False
    assert result["broker_submit_calls"] == 0
    assert submit_calls == []
    assert orch.simulated_broker.submit_calls == 0

    decisions = repo.list_decisions()
    assert len(decisions) == 1
    assert decisions[0]["action"] == "HOLD"
    assert decisions[0]["confidence"] == 1.0
    latest = repo.latest_decision("SPY")
    assert latest is not None
    assert latest["action"] == "HOLD"
    assert latest["analysis_ref"]
    assert repo.list_trades() == []

    with pytest.raises(OrderPlacementDisabledError):
        orch.place_order()
    repo.close()
