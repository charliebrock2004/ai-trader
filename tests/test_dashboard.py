from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_and_home(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "AI-Trader" in home.text
    assert "Account balance" in home.text
    assert "profit/loss" in home.text
    assert "Grok" in home.text
    assert "Current BUY / SELL / HOLD decision" in home.text
    assert "Current position" in home.text
    assert "Start" in home.text
    assert "Stop" in home.text
    assert "Performance" in home.text
    assert "PAPER SIMULATION" in home.text
    assert "NO REAL TRADING" in home.text
    assert "Kill switch" not in home.text
    assert "BROKER NOT USED" not in home.text
    assert "Pipeline" not in home.text
    assert "Event log" not in home.text

    status = client.get("/api/status")
    assert status.status_code == 200
    body = status.json()
    assert body["orders_enabled"] is False
    assert body["safety"]["live_trading_allowed"] is False
    assert body["kill_switch"]["engaged"] is True
    assert body["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert body["broker_used"] is False


def test_system_page_keeps_safety_controls(client: TestClient) -> None:
    page = client.get("/system")
    assert page.status_code == 200
    assert "Kill switch" in page.text
    assert "PAPER SIMULATION" in page.text
    assert "NO REAL TRADING" in page.text
    assert "BROKER NOT USED" in page.text
    assert "Pipeline" in page.text
    assert "Event log" in page.text
    assert "Modules" in page.text


def test_orders_endpoint_forbidden(client: TestClient) -> None:
    response = client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1})
    assert response.status_code == 403


def test_kill_switch_roundtrip(client: TestClient) -> None:
    off = client.post(
        "/api/kill-switch",
        json={"engaged": False, "reason": "test"},
    )
    assert off.status_code == 200
    assert off.json()["kill_switch"]["engaged"] is False

    dry = client.post("/api/dry-run")
    assert dry.status_code == 200
    body = dry.json()
    assert body["orders_placed"] == 0
    assert body["fills"] == 0
    assert body["risk_approved"] is False
    assert body["broker_submit_calls"] == 0
    assert body["account"]["cash"] == 100.00
    assert body["account"]["positions"] == []
    assert len(body["market"]) >= 1
    assert len(body["analysis"]) >= 1
    assert all(row["action"] == "HOLD" for row in body["decisions"])
    market = client.get("/api/market")
    assert market.status_code == 200
    assert market.json()["series"]
    analysis = client.get("/api/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["analysis"]
    decision = client.get("/api/decision")
    assert decision.status_code == 200
    assert decision.json()["decision"]["action"] == "HOLD"
    account = client.get("/api/account")
    assert account.status_code == 200
    assert account.json()["account"]["cash"] == 100.00
    assert account.json()["account"]["fill_count"] == 0

    on = client.post(
        "/api/kill-switch",
        json={"engaged": True, "reason": "test halt"},
    )
    assert on.json()["kill_switch"]["engaged"] is True


def test_empty_ledgers(client: TestClient) -> None:
    assert client.get("/api/decisions").json()["decisions"] == []
    assert client.get("/api/trades").json()["trades"] == []
    assert client.get("/api/positions").json()["positions"] == []
    assert client.get("/api/decision").json()["decision"] is None


def test_paper_sim_is_offline(client: TestClient) -> None:
    client.post("/api/kill-switch", json={"engaged": False, "reason": "test"})
    sim = client.post("/api/paper-sim")
    assert sim.status_code == 200
    body = sim.json()
    assert body["ok"] is True
    assert body["live"] is False
    assert body["look_ahead"] is False
    assert body["broker_submit_calls"] == 0
    assert body["account"]["currency"] == "GBP"
    paper = client.get("/api/paper")
    assert paper.status_code == 200
    assert client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1}).status_code == 403


def test_grok_paper_cycle_defaults_to_fixture(client: TestClient) -> None:
    client.post("/api/kill-switch", json={"engaged": False, "reason": "test"})
    cycle = client.post("/api/grok-paper-cycle")
    assert cycle.status_code == 200
    body = cycle.json()
    assert body["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert body["broker"] == "NOT USED"
    assert body["ai_model"] == "fixture-hold"
    assert body["ai_decision"]["action"] == "HOLD"
    assert body["broker_submit_calls"] == 0
    assert client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1}).status_code == 403


def test_benchmark_is_paper_only(client: TestClient) -> None:
    empty = client.get("/api/benchmark")
    assert empty.status_code == 200
    assert empty.json()["live"] is False
    run = client.post("/api/benchmark")
    assert run.status_code == 200
    body = run.json()
    assert body["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert body["broker"] == "NOT USED"
    assert body["broker_submit_calls"] == 0
    assert body["live"] is False
    assert body["grok_model"] == "fixture-hold"
    assert len(body["comparison"]) == 4
    stored = client.get("/api/benchmark")
    assert stored.json()["run_count"] == body["run_count"]
    assert stored.json()["run_count"] == 5 * 3 * 4
    assert client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1}).status_code == 403


def test_performance_page(client: TestClient) -> None:
    page = client.get("/performance")
    assert page.status_code == 200
    assert "Performance" in page.text
    assert "PAPER SIMULATION" in page.text
    assert "Grok return" in page.text


def test_paper_session_is_offline(client: TestClient) -> None:
    page = client.get("/paper")
    assert page.status_code == 200
    assert "Account balance" in page.text
    assert "PAPER SIMULATION" in page.text
    idle = client.get("/api/paper-session")
    assert idle.status_code == 200
    assert idle.json()["live"] is False
    assert idle.json()["broker"] == "NOT USED"
    assert idle.json()["grok"] == "STOPPED"
    run = client.post("/api/paper-session/start", json={"symbol": "SIM-UP", "bars": 16, "warmup": 8, "grok_frequency": 8})
    assert run.status_code == 200
    body = run.json()
    assert body["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert body["broker"] == "NOT USED"
    assert body["broker_submit_calls"] == 0
    assert body["live"] is False
    assert body["real_market_data"] is False
    assert body["look_ahead"] is False
    stopped = client.post("/api/paper-session/stop")
    assert stopped.json()["grok"] == "STOPPED"
    assert client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1}).status_code == 403


def test_continuous_start_is_paper_only(client: TestClient) -> None:
    run = client.post(
        "/api/paper-session/start",
        json={
            "symbol": "SIM-UP",
            "bars": 12,
            "warmup": 4,
            "grok_frequency": 4,
            "continuous": True,
        },
    )
    assert run.status_code == 200
    body = run.json()
    assert body["banner"] == "PAPER SIMULATION — NO REAL TRADING"
    assert body["live"] is False
    assert body["broker"] == "NOT USED"
    assert body["grok"] in {"RUNNING", "STARTING"}
    assert body["running"] is True
    stopped = client.post("/api/paper-session/stop")
    assert stopped.json()["grok"] == "STOPPED"
    assert stopped.json()["live"] is False
    assert client.post("/api/orders", json={"symbol": "SPY", "side": "BUY", "qty": 1}).status_code == 403
