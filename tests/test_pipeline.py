from __future__ import annotations

import pytest

from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.config import Settings
from ai_trader.db.repository import Repository
from ai_trader.exceptions import FoundationModeError, OrderPlacementDisabledError
from ai_trader.kill_switch import KillSwitch
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.types import MarketSnapshot, utc_now_iso


def test_grok_does_not_call_api(isolated_env: object) -> None:
    analyst = GrokAnalyst(Settings())
    snapshot = MarketSnapshot(as_of=utc_now_iso(), bars=tuple(), source="test")
    with pytest.raises(FoundationModeError):
        analyst.propose(snapshot)
    assert analyst.enabled is False


def test_dry_run_blocked_by_kill_switch(isolated_env: object) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    result = orch.dry_run()
    assert result["ok"] is False
    assert result["blocked_by"] == "kill_switch"
    assert orch.place_order is not None
    with pytest.raises(OrderPlacementDisabledError):
        orch.place_order()
    repo.close()


def test_dry_run_records_event_and_places_zero_orders(isolated_env: object) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)
    result = orch.dry_run(["SPY"])
    assert result["ok"] is True
    assert result["orders_placed"] == 0
    assert repo.list_trades() == []
    events = repo.list_events()
    assert any(event["event_type"] == "dry_run" for event in events)
    repo.close()
