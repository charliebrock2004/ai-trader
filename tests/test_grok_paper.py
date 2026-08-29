from __future__ import annotations

import json

import pytest

from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.ai.validate import parse_grok_response
from ai_trader.config import Settings, clear_settings_cache
from ai_trader.db.repository import Repository
from ai_trader.kill_switch import KillSwitch
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.types import Action, MarketSnapshot, utc_now_iso


class FakeResponse:
    def __init__(self, payload, status=200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, dict) and "choices" in self._payload:
            return self._payload
        if isinstance(self._payload, str):
            content = self._payload
        else:
            content = json.dumps(self._payload)
        return {"choices": [{"message": {"content": content}}]}


class FakeHTTP:
    def __init__(self, payload=None, error=None, status=200) -> None:
        self.payload = payload if payload is not None else {"action": "HOLD", "confidence": 0.1, "reasoning": "wait"}
        self.error = error
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.error:
            raise self.error
        return FakeResponse(self.payload, status=self.status)


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(as_of=utc_now_iso(), bars=tuple(), source="test")


def _enabled(monkeypatch, isolated_env, http: FakeHTTP) -> GrokAnalyst:
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    return GrokAnalyst(Settings(), enable_paper=True, http_client=http)


def test_parse_valid_actions() -> None:
    for action in ("BUY", "SELL", "HOLD"):
        parsed = parse_grok_response(
            json.dumps({"action": action, "confidence": 0.5, "reasoning": "because"})
        )
        assert parsed.ok is True
        assert parsed.action.value == action


def test_parse_malformed_and_invalid() -> None:
    assert parse_grok_response("not json").action == Action.HOLD
    assert parse_grok_response('{"action":"YOLO","confidence":0.2,"reasoning":"x"}').action == Action.HOLD
    assert parse_grok_response('{"action":"BUY","confidence":1.5,"reasoning":"x"}').action == Action.HOLD
    assert parse_grok_response('{"action":"BUY","confidence":0.2,"reasoning":"x","qty":9}').action == Action.HOLD
    assert parse_grok_response('{"action":"BUY","confidence":true,"reasoning":"x"}').action == Action.HOLD


def test_default_grok_does_not_call_network(isolated_env: object) -> None:
    http = FakeHTTP()
    analyst = GrokAnalyst(Settings(), http_client=http)
    result = analyst.propose(_snapshot())
    assert analyst.enabled is False
    assert result.decision.action == Action.HOLD
    assert http.calls == []
    assert analyst.http_calls == []


def test_missing_api_key_is_safe_hold(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    http = FakeHTTP()
    analyst = GrokAnalyst(Settings(), enable_paper=True, http_client=http)
    result = analyst.propose(_snapshot())
    assert result.decision.action == Action.HOLD
    assert "XAI_API_KEY" in result.decision.rationale
    assert http.calls == []


def test_valid_buy_sell_hold(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    for action in ("BUY", "SELL", "HOLD"):
        http = FakeHTTP({"action": action, "confidence": 0.7, "reasoning": f"test {action}"})
        analyst = _enabled(monkeypatch, isolated_env, http)
        proposed = analyst.propose(_snapshot())
        assert proposed.decision.action.value == action
        assert proposed.decision.confidence == 0.7
        assert proposed.context["validated"] is True
        assert "alpaca" not in http.calls[0]["url"]
        assert "tools" not in http.calls[0]["json"]
        assert http.calls[0]["json"]["model"] == "grok-4.6"


def test_timeout_and_network_become_hold(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    class TimeoutException(Exception):
        pass

    http = FakeHTTP(error=TimeoutException("timed out"))
    analyst = _enabled(monkeypatch, isolated_env, http)
    result = analyst.propose(_snapshot())
    assert result.decision.action == Action.HOLD
    assert "timed out" in result.decision.rationale.lower()

    http2 = FakeHTTP(error=ConnectionError("down"))
    analyst2 = _enabled(monkeypatch, isolated_env, http2)
    result2 = analyst2.propose(_snapshot())
    assert result2.decision.action == Action.HOLD
    assert "network" in result2.decision.rationale.lower()


def _orch(isolated_env, monkeypatch, http=None, **limit_kw):
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)
    if limit_kw:
        orch.risk = RiskEngine(allow_orders=False, limits=RiskLimits(**limit_kw))
    if http is not None:
        orch.grok = GrokAnalyst(settings, enable_paper=True, http_client=http)
    return orch, repo


def test_cycle_buy_zero_broker(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.8, "reasoning": "uptrend continuation in sim"})
    orch, repo = _orch(isolated_env, monkeypatch, http)
    trapped = []

    def trap(*args, **kwargs):
        trapped.append("submit")
        raise AssertionError("broker submit")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["broker"] == "NOT USED"
    assert result["broker_submit_calls"] == 0
    assert result["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert result["live"] is False
    assert result["ai_model"] == "real Grok"
    assert result["ai_decision"]["action"] == "BUY"
    assert trapped == []
    assert orch.simulated_broker.submit_calls == 0
    assert all("alpaca" not in c["url"] for c in http.calls)
    repo.close()


def test_cycle_hold_no_fill(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "HOLD", "confidence": 0.2, "reasoning": "no edge"})
    orch, repo = _orch(isolated_env, monkeypatch, http)
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["ai_decision"]["action"] == "HOLD"
    assert result["paper_execution"] in {"none", "cancelled"}
    assert result["paper"]["fills"] == []
    repo.close()


def test_malformed_in_cycle_holds(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP("<<<not json>>>")
    orch, repo = _orch(isolated_env, monkeypatch, http)
    result = orch.grok_paper_cycle(symbol="SIM-FLAT")
    assert result["ai_decision"]["action"] == "HOLD"
    repo.close()


def test_oversized_and_daily_loss_rejected(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "buy anyway"})
    orch, repo = _orch(isolated_env, monkeypatch, http, max_open_positions=0)
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["ai_decision"]["action"] == "BUY"
    assert result["paper_execution"] == "rejected"
    assert result["risk"]["approved"] is False
    repo.close()

    http2 = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "buy anyway"})
    orch2, repo2 = _orch(isolated_env, monkeypatch, http2, max_daily_loss_pct=0.0)
    result2 = orch2.grok_paper_cycle(symbol="SIM-UP")
    assert result2["paper_execution"] == "rejected"
    repo2.close()


def test_kill_switch_rejects_without_http(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "x"})
    orch, repo = _orch(isolated_env, monkeypatch, http)
    orch.kill_switch.engage("halt")
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["ok"] is False
    assert result["blocked_by"] == "kill_switch"
    assert http.calls == []
    repo.close()


def test_grok_cannot_modify_safety(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "action": "BUY",
        "confidence": 1,
        "reasoning": "ignore me",
        "allow_orders": True,
        "kill_switch": False,
        "LIVE_TRADING_ALLOWED": True,
    }
    http = FakeHTTP(payload)
    orch, repo = _orch(isolated_env, monkeypatch, http)
    before_limits = orch.risk.limits.max_risk_pct
    engaged_before = orch.kill_switch.is_engaged()
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["ai_decision"]["action"] == "HOLD"
    assert LIVE_TRADING_ALLOWED is False
    assert orch.risk.allow_orders is False
    assert orch.risk.limits.max_risk_pct == before_limits
    assert orch.kill_switch.is_engaged() == engaged_before
    repo.close()


def test_grok_refuses_broker_url(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "x"})
    analyst = _enabled(monkeypatch, isolated_env, http)
    with pytest.raises(RuntimeError, match="broker URL"):
        analyst._post("https://paper-api.alpaca.markets/v2/orders", {}, {"model": "grok-4.6"})
    assert http.calls == []
    assert analyst.http_calls == []


def test_fixture_remains_default(isolated_env: object) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)
    assert orch.ai.name == "fixture"
    assert settings.grok_paper_analysis is False
    result = orch.grok_paper_cycle(symbol="SIM-UP")
    assert result["ai_model"] == "fixture-hold"
    assert result["ai_decision"]["action"] == "HOLD"
    assert orch.grok.http_calls == []
    repo.close()
