"""The CPI event pipeline is parked: off by default, code and tests kept."""

from __future__ import annotations

from ai_trader.agent.cycle import EVENT_DECISION_KIND
from ai_trader.config import Settings, clear_settings_cache
from ai_trader.rpc import handle
from ai_trader.runtime import get_runtime, reset_runtime


def test_the_event_pipeline_is_off_by_default(isolated_env) -> None:
    assert Settings().event_pipeline_enabled is False
    assert Settings().public_view()["event_pipeline_enabled"] is False


def test_a_parked_pipeline_runs_no_cycle_and_registers_no_contracts(isolated_env) -> None:
    runtime = get_runtime()
    calls: list[int] = []
    runtime.agent.run_cycle = lambda: calls.append(1)  # type: ignore[method-assign]

    result = handle({"cmd": "cycle"})

    assert calls == []
    assert result["ok"] is False and result["parked"] is True
    assert runtime.agent.market.discover() == []


def test_a_parked_pipeline_is_left_out_of_status_and_the_funnel(isolated_env) -> None:
    runtime = get_runtime()
    assert "cpi_event" not in runtime.worker.status()["pipelines"]

    runtime.agent.store.pipeline_funnel = lambda: {  # type: ignore[attr-defined]
        "spot": {"considered": 3},
        EVENT_DECISION_KIND: {"considered": 4380},
    }
    assert list(runtime.agent.performance()["pipelines"]) == ["spot"]


def test_the_switch_turns_the_pipeline_back_on(isolated_env, monkeypatch) -> None:
    monkeypatch.setenv("EVENT_PIPELINE_ENABLED", "true")
    clear_settings_cache()
    reset_runtime()
    runtime = get_runtime()
    calls: list[int] = []
    runtime.agent.run_cycle = lambda: calls.append(1) or {"ok": True}  # type: ignore[method-assign]

    assert handle({"cmd": "cycle"}) == {"ok": True}
    assert calls == [1]
    assert "cpi_event" in runtime.worker.status()["pipelines"]
    assert runtime.agent.event_pipeline_enabled is True


def test_a_parked_pipeline_is_not_reported_as_a_broken_component(isolated_env) -> None:
    system = get_runtime().agent.system()
    assert {c["id"] for c in system["components"]}.isdisjoint({"events", "market"})
    assert system["event_family"] == "parked"
