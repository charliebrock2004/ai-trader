from __future__ import annotations

import pytest

from ai_trader.ai.fixture import FixtureAnalyst
from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.config import Settings, clear_settings_cache
from ai_trader.db.repository import Repository
from ai_trader.exceptions import HistoricalDataNotConfiguredError
from ai_trader.kill_switch import KillSwitch
from ai_trader.market_data.generator import generate_series
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.risk.engine import RiskEngine
from ai_trader.risk.limits import RiskLimits
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.config import PaperSessionConfig
from ai_trader.session.runner import PaperSession
from tests.test_grok_paper import FakeHTTP


def _enabled(monkeypatch, http: FakeHTTP) -> GrokAnalyst:
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    clear_settings_cache()
    return GrokAnalyst(Settings(), enable_paper=True, http_client=http)


def test_repeated_decisions_are_sequential(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = [
        {"action": "HOLD", "confidence": 0.2, "reasoning": "wait 1"},
        {"action": "BUY", "confidence": 0.8, "reasoning": "enter 2"},
        {"action": "HOLD", "confidence": 0.3, "reasoning": "wait 3"},
        {"action": "SELL", "confidence": 0.7, "reasoning": "exit 4"},
        {"action": "HOLD", "confidence": 0.4, "reasoning": "wait 5"},
    ]
    http = FakeHTTP(payloads[0])
    calls = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        http.payload = payload
        return FakeHTTP.post(http, url, headers=headers, json=json, timeout=timeout)

    http.post = post  # type: ignore[method-assign]
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=30, warmup=5, grok_frequency=5),
        analyst=analyst,
    )
    report = session.start()
    assert report["look_ahead"] is False
    assert report["broker_submit_calls"] == 0
    assert report["live"] is False
    assert report["decisions"] >= 4
    bars = [d["bar"] for d in report["ai_decisions"]]
    assert bars == sorted(bars)
    assert all(d["bar_count"] == d["bar"] + 1 for d in report["ai_decisions"])
    assert all(
        d.get("analysis_bar_count") in (None, d["bar_count"])
        for d in report["ai_decisions"]
    )
    assert len(http.calls) == report["decisions"]
    assert all("alpaca" not in c["url"] for c in http.calls)
    assert all("tools" not in c["json"] for c in http.calls)


def test_no_look_ahead_on_every_consult(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "HOLD", "confidence": 0.2, "reasoning": "flat"})
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-FLAT", bars=24, warmup=6, grok_frequency=6),
        analyst=analyst,
    )
    report = session.start()
    assert report["look_ahead"] is False
    for decision in report["ai_decisions"]:
        assert decision["bar_count"] == decision["bar"] + 1


def test_stop_blocks_new_trades(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "buy"})
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=40, warmup=5, grok_frequency=5),
        analyst=analyst,
    )
    report = session.start(stop_at=12)
    assert report["stopped_at"] == 12
    assert report["grok"] == "STOPPED"
    assert all(d["bar"] < 12 for d in report["ai_decisions"])
    assert len(http.calls) == report["decisions"]
    assert report["broker"] == "NOT USED"


def test_risk_rejection_does_not_fill(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "buy anyway"})
    analyst = _enabled(monkeypatch, http)
    risk = RiskEngine(allow_orders=False, limits=RiskLimits(max_open_positions=0))
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=24, warmup=8, grok_frequency=8),
        analyst=analyst,
        risk=risk,
    )
    report = session.start()
    assert report["ai_decisions"]
    assert all(d["action"] == "BUY" for d in report["ai_decisions"])
    assert report["trades"] == 0
    assert report["fills"] == []
    assert any(o.get("status") == "REJECTED" for o in report["orders"])
    assert report["broker_submit_calls"] == 0


def test_account_updates_after_fill(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP({"action": "BUY", "confidence": 0.85, "reasoning": "trend"})
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=24, warmup=8, grok_frequency=8),
        analyst=analyst,
    )
    report = session.start()
    assert report["account"]["starting_cash"] == 100.0
    if report["fills"]:
        assert report["account"]["account_equity"] != 0
        assert report["balance"] == report["account"]["account_equity"]
    assert report["live"] is False


def test_position_management_sell_closes(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    cycle = [
        {"action": "BUY", "confidence": 0.8, "reasoning": "in"},
        {"action": "HOLD", "confidence": 0.4, "reasoning": "wait"},
        {"action": "SELL", "confidence": 0.8, "reasoning": "out"},
    ]
    http = FakeHTTP(cycle[0])
    n = {"i": 0}

    def post(url, headers=None, json=None, timeout=None):
        payload = cycle[min(n["i"], len(cycle) - 1)]
        n["i"] += 1
        http.payload = payload
        return FakeHTTP.post(http, url, headers=headers, json=json, timeout=timeout)

    http.post = post  # type: ignore[method-assign]
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(
            symbol="SIM-UP",
            bars=30,
            warmup=8,
            grok_frequency=8,
            flatten_at_end=False,
        ),
        analyst=analyst,
    )
    report = session.start()
    actions = [d["action"] for d in report["ai_decisions"]]
    assert "BUY" in actions
    assert "SELL" in actions
    buy_fills = [f for f in report["fills"] if f.get("side") == "BUY"]
    sell_fills = [f for f in report["fills"] if f.get("side") == "SELL"]
    if buy_fills:
        assert sell_fills, "SELL must close an open paper long."
        assert report["position"] == "flat"
        assert report["account"]["invested_value"] == 0 or report["open_pnl"] == 0
    assert report["broker_submit_calls"] == 0
    assert LIVE_TRADING_ALLOWED is False


def test_timeout_and_malformed_become_hold(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP(error=TimeoutError("timed out"))
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-FLAT", bars=16, warmup=8, grok_frequency=8),
        analyst=analyst,
    )
    report = session.start()
    assert report["ai_decisions"]
    assert all(d["action"] == "HOLD" for d in report["ai_decisions"])
    assert report["trades"] == 0

    http2 = FakeHTTP("<<<not json>>>")
    analyst2 = _enabled(monkeypatch, http2)
    session2 = PaperSession(
        PaperSessionConfig(symbol="SIM-FLAT", bars=16, warmup=8, grok_frequency=8),
        analyst=analyst2,
    )
    report2 = session2.start()
    assert all(d["action"] == "HOLD" for d in report2["ai_decisions"])


def test_network_failure_is_hold(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHTTP(error=ConnectionError("down"))
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-VOL", bars=16, warmup=8, grok_frequency=8),
        analyst=analyst,
    )
    report = session.start()
    assert all(d["action"] == "HOLD" for d in report["ai_decisions"])
    assert report["broker"] == "NOT USED"


def test_fixture_default_session_zero_broker(isolated_env) -> None:
    session = PaperSession(PaperSessionConfig(symbol="SIM-UP", bars=20, warmup=8, grok_frequency=8))
    report = session.start()
    assert report["grok_model"] == "fixture-hold"
    assert report["grok"] == "STOPPED"
    assert report["broker_submit_calls"] == 0
    assert report["live"] is False
    assert report["real_market_data"] is False
    assert report["decisions"] >= 1
    assert all(d["action"] == "HOLD" for d in report["ai_decisions"])
    assert report["trades"] == 0


def test_historical_source_refused() -> None:
    with pytest.raises(HistoricalDataNotConfiguredError):
        PaperSessionConfig(source="historical").validate()


def test_orchestrator_session_never_calls_broker(isolated_env) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    trapped: list[str] = []

    def trap(*args, **kwargs):
        trapped.append("submit")
        raise AssertionError("broker submit")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]
    result = orch.start_paper_session(symbol="SIM-UP", bars=16, grok_frequency=8, warmup=8)
    assert result["broker"] == "NOT USED"
    assert result["broker_submit_calls"] == 0
    assert trapped == []
    assert orch.simulated_broker.submit_calls == 0
    assert orch.alpaca_broker.health()["connected"] is False
    # The paper run is persisted even when the SMA filter never fires and
    # Grok is not called. Empty Grok rows are honest, not a missing audit.
    assert repo.list_paper_orders(50) is not None
    perf = repo.latest_performance()
    assert perf is not None
    stopped = orch.stop_paper_session()
    assert stopped["grok"] == "STOPPED"
    repo.close()


def test_orchestrator_public_session_never_calls_broker(isolated_env) -> None:
    from datetime import datetime, timedelta, timezone

    from ai_trader.market_data.public import PublicCryptoFeed

    now = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(16):
        start = now - timedelta(seconds=300 * (16 - i))
        price = 100000.0 + i
        rows.append([int(start.timestamp()), price - 20, price + 20, price, price + 1, 2.0])
    rows.reverse()

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Get:
        def get(self, url, headers=None, params=None, timeout=None):
            assert "alpaca" not in url.lower()
            return _Resp(rows)

    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    trapped: list[str] = []

    def trap(*args, **kwargs):
        trapped.append("submit")
        raise AssertionError("broker submit")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]
    orch.public_market_data = PublicCryptoFeed(http_client=_Get(), now_fn=lambda: now)
    result = orch.start_paper_session(
        symbol="BTC-USD",
        bars=16,
        grok_frequency=8,
        warmup=8,
        source="public",
    )
    assert result["broker"] == "NOT USED"
    assert result["broker_submit_calls"] == 0
    assert result["live"] is False
    assert result["look_ahead"] is False
    # A public session either gets real prices or fails closed. Both outcomes
    # are acceptable here — this machine may have no egress — but the payload
    # has to say which one happened. Asserting `real_market_data is True`
    # unconditionally used to pass even when the session had died on the FX
    # lookup and held no prices at all, which is the claim this system must
    # never make.
    if result["ok"]:
        assert result["real_market_data"] is True
        assert result["last_price"] is not None
    else:
        assert result["real_market_data"] is False
        assert result["running"] is False
        assert result["data_error"], "a failed session must say why"
    assert trapped == []
    assert orch.alpaca_broker.health()["connected"] is False
    repo.close()


def test_stop_from_another_thread_blocks_new_trades(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    http = FakeHTTP({"action": "BUY", "confidence": 0.9, "reasoning": "buy"})

    def post(url, headers=None, json=None, timeout=None):
        time.sleep(0.12)
        return FakeHTTP.post(http, url, headers=headers, json=json, timeout=timeout)

    http.post = post  # type: ignore[method-assign]
    analyst = _enabled(monkeypatch, http)
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=40, warmup=4, grok_frequency=4),
        analyst=analyst,
    )
    holder: dict[str, object] = {}

    def run() -> None:
        holder["report"] = session.start()

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.time() + 3
    while not session.running and worker.is_alive() and time.time() < deadline:
        time.sleep(0.01)
    stopped = session.stop()
    worker.join(timeout=8)
    assert stopped["grok"] == "STOPPED"
    assert session.stopped is True
    report = holder.get("report") or session.status()
    assert report["broker"] == "NOT USED"
    assert report["live"] is False
    assert report["broker_submit_calls"] == 0


def test_visible_bars_never_include_future() -> None:
    from dataclasses import replace

    series = generate_series("SIM-UP", limit=20, seed=3)
    from ai_trader.session.source import RepeatingGrokSource

    source = RepeatingGrokSource(FixtureAnalyst(), frequency=1, warmup=0)
    for i in range(len(series.candles)):
        visible = replace(series, candles=series.candles[: i + 1])
        source.decide(i, visible, None)


def test_continuous_start_shows_running_until_stop(isolated_env) -> None:
    session = PaperSession(
        PaperSessionConfig(symbol="SIM-UP", bars=12, warmup=4, grok_frequency=4, continuous=True),
        poll_seconds=0.05,
    )
    report = session.start()
    assert report["live"] is False
    assert report["broker"] == "NOT USED"
    assert report["broker_submit_calls"] == 0
    assert report["grok"] == "RUNNING"
    assert report["running"] is True
    live = session.status()
    assert live["grok"] == "RUNNING"
    stopped = session.stop()
    if session._thread is not None:
        session._thread.join(timeout=2)
    assert stopped["grok"] == "STOPPED"
    assert session.stopped is True
    assert session.running is False


def test_extend_processes_only_new_bars(isolated_env) -> None:
    from dataclasses import replace

    from ai_trader.paper.simulator import PaperSimulator

    series = generate_series("SIM-UP", timeframe="5m", limit=16, seed=42, source="simulated")
    sim = PaperSimulator(flatten_at_end=False)
    first = replace(series, candles=series.candles[:10])
    sim.run(first, finalize=False)
    before = [row["bar"] for row in sim.equity_curve]
    assert before == list(range(10))
    sim.extend(series, start_index=10, finalize=False)
    after = [row["bar"] for row in sim.equity_curve]
    assert after[:10] == list(range(10))
    assert after[-6:] == list(range(10, 16))
    assert sim.seen_future is False


def test_orchestrator_continuous_session_never_calls_broker(isolated_env) -> None:
    settings = Settings()
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    trapped: list[str] = []

    def trap(*args, **kwargs):
        trapped.append("submit")
        raise AssertionError("broker submit")

    orch.broker.submit = trap  # type: ignore[method-assign]
    orch.simulated_broker.submit = trap  # type: ignore[method-assign]
    orch.alpaca_broker.submit = trap  # type: ignore[method-assign]
    result = orch.start_paper_session(
        symbol="SIM-UP",
        bars=12,
        grok_frequency=4,
        warmup=4,
        continuous=True,
    )
    assert result["grok"] == "RUNNING"
    assert result["live"] is False
    assert result["broker"] == "NOT USED"
    assert trapped == []
    stopped = orch.stop_paper_session()
    if orch.paper_session._thread is not None:
        orch.paper_session._thread.join(timeout=2)
    assert stopped["grok"] == "STOPPED"
    assert orch.alpaca_broker.health()["connected"] is False
    repo.close()
