"""The HTTP worker: the transport the deployed frontend actually talks to.

These tests exist because a deployment can fail in ways the engine tests cannot
see. The service refused to boot at all until recently — the host was running
``python -m ai_trader http`` against a CLI that had no such command — and a
transport that answers 401 to every Start is just as broken as one that will
not start.

So the properties under test here are transport properties: the command exists,
it binds where the host expects, reads cannot mutate, mutations cannot happen
without the token, and nothing on the way through invents a price, a fill or a
RUNNING state.
"""

from __future__ import annotations

from pathlib import Path

import ai_trader.__main__ as cli
from fastapi.testclient import TestClient

from ai_trader.http_api import create_app
from ai_trader.rpc import MUTATING_COMMANDS, READ_COMMANDS
from ai_trader.safety import LIVE_TRADING_ALLOWED

TOKEN = "test-control-token-0123456789"
HEADERS = {"x-ai-trader-token": TOKEN}

SIMULATED = {
    "symbol": "SIM-UP",
    "source": "simulated",
    "timeframe": "5m",
    "bars": 12,
    "warmup": 4,
    "grok_frequency": 4,
}


def _client(monkeypatch, *, token: str | None = TOKEN) -> TestClient:
    """A client with no lifespan, so tests never race the boot thread."""
    if token is None:
        monkeypatch.delenv("AI_TRADER_API_TOKEN", raising=False)
        monkeypatch.delenv("API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AI_TRADER_API_TOKEN", token)
    return TestClient(create_app())


# ==========================================================================
# The deployment blocker itself
# ==========================================================================
def test_the_http_command_the_host_runs_actually_exists() -> None:
    """`python -m ai_trader http` must be a real command.

    The deployed service failed with `invalid choice: 'http'`. A test that only
    exercised the FastAPI app would still have passed, so this asserts on the
    argument parser the host's start command reaches.
    """
    body = Path(cli.__file__).read_text(encoding="utf-8")
    assert '"http"' in body, "the http command must be registered in the CLI"
    assert "serve_http" in body, "the http command must reach the HTTP server"

    # And it must survive the argument parser rather than only appearing in the
    # source: `main(["http"])` is what the host's start command becomes.
    called: list[str] = []
    import ai_trader.http_api as http_api

    original = http_api.serve
    http_api.serve = lambda: called.append("served") or 0  # type: ignore[assignment]
    try:
        assert cli.main(["http"]) == 0
    finally:
        http_api.serve = original  # type: ignore[assignment]
    assert called == ["served"], "`ai_trader http` must start the HTTP worker"


def test_http_serve_is_importable_and_binds_the_hosts_port(monkeypatch) -> None:
    """The host injects PORT. Binding anything else fails the deploy."""
    from ai_trader.config import clear_settings_cache, get_settings

    monkeypatch.setenv("PORT", "10000")
    clear_settings_cache()
    try:
        assert get_settings().worker_port == 10000
    finally:
        monkeypatch.delenv("PORT", raising=False)
        clear_settings_cache()


# ==========================================================================
# Health and reads
# ==========================================================================
def test_health_answers_without_touching_the_engine(isolated_env, monkeypatch) -> None:
    """The platform polls /health to decide the deploy is live.

    It must answer while the agent is still waking up, so it cannot depend on
    the database, the calendar or the FX feed.
    """
    client = _client(monkeypatch)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["live"] is False
    assert body["live_trading_allowed"] is False
    assert body["control_enabled"] is True
    assert response.headers["cache-control"] == "no-store"


def test_health_reports_control_enabled_when_no_token_is_set(isolated_env, monkeypatch) -> None:
    """A free paper desk still accepts Start when no token is configured."""
    client = _client(monkeypatch, token=None)
    assert client.get("/health").json()["control_enabled"] is True


def test_status_reports_a_stopped_hundred_pound_desk(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    body = client.get("/api/status").json()
    assert body["running"] is False
    assert body["stopped"] is True
    assert body["status"] == "STOPPED"
    assert float(body["balance"]) == 100.0
    assert body["currency"] == "GBP"
    assert body["live"] is False
    assert body["engine"] == "python-worker"


def test_every_read_route_is_a_read_command(isolated_env, monkeypatch) -> None:
    """No GET may resolve to something that changes state."""
    client = _client(monkeypatch)
    for path in (
        "/api/status",
        "/api/agent",
        "/api/performance",
        "/api/system",
        "/api/decisions",
        "/api/opportunities",
    ):
        assert client.get(path).status_code == 200, path
    assert MUTATING_COMMANDS.isdisjoint(READ_COMMANDS)


def test_a_mutating_path_refuses_a_get(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    for path in ("/api/start", "/api/stop", "/api/cycle"):
        assert client.get(path).status_code == 405, path


def test_decision_ids_are_validated_not_trusted(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/api/decisions/0").status_code == 422
    assert client.get("/api/decisions/not-a-number").status_code == 422
    assert client.get("/api/decisions?limit=99999").status_code == 422


# ==========================================================================
# Control is gated
# ==========================================================================
def test_an_unconfigured_worker_allows_paper_mutations(isolated_env, monkeypatch) -> None:
    """No token means this paper desk can still be started from the UI."""
    client = _client(monkeypatch, token=None)
    started = client.post("/api/start", json=SIMULATED)
    assert started.status_code == 200
    body = started.json()
    assert body["ok"] is True
    assert body["running"] is True
    assert body["live"] is False
    stopped = client.post("/api/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


def test_a_missing_or_wrong_token_cannot_control_the_desk(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.post("/api/start", json=SIMULATED).status_code == 401
    assert (
        client.post(
            "/api/start", json=SIMULATED, headers={"x-ai-trader-token": "wrong"}
        ).status_code
        == 401
    )
    # A near-miss must not pass either.
    assert (
        client.post(
            "/api/start", json=SIMULATED, headers={"x-ai-trader-token": TOKEN + "x"}
        ).status_code
        == 401
    )
    assert client.get("/api/status").json()["running"] is False


def test_a_refused_mutation_never_claims_the_desk_is_running(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    body = client.post("/api/start", json=SIMULATED).json()
    assert body["running"] is False
    assert body["stopped"] is True
    assert body["live"] is False
    assert body["live_trading_allowed"] is False


def test_the_control_rate_limit_stops_a_retry_loop(isolated_env, monkeypatch) -> None:
    """A stuck tab retrying Start spends the model budget as fast as an attacker."""
    client = _client(monkeypatch)
    codes = {client.post("/api/stop", headers=HEADERS).status_code for _ in range(25)}
    assert 429 in codes


# ==========================================================================
# Start and Stop are real
# ==========================================================================
def test_start_then_stop_moves_real_state(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)

    started = client.post("/api/start", json=SIMULATED, headers=HEADERS)
    assert started.status_code == 200
    body = started.json()
    assert body["running"] is True
    assert body["stopped"] is False
    assert body["status"] in {"STARTING", "RUNNING"}
    assert body["live"] is False
    assert float(body["balance"]) == 100.0

    status = client.get("/api/status").json()
    assert status["running"] is True
    assert status["worker_alive"] is True

    stopped = client.post("/api/stop", headers=HEADERS)
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert client.get("/api/status").json()["status"] == "STOPPED"


def test_duplicate_starts_do_not_replace_a_live_session(isolated_env, monkeypatch) -> None:
    """A double-clicked button must not reset the session behind the desk."""
    client = _client(monkeypatch)
    first = client.post("/api/start", json=SIMULATED, headers=HEADERS).json()
    assert first["running"] is True
    bars = first.get("bars")

    for _ in range(3):
        again = client.post("/api/start", json=SIMULATED, headers=HEADERS).json()
        assert again["running"] is True
        assert float(again["balance"]) == float(first["balance"])
    assert client.get("/api/status").json().get("bars") == bars
    client.post("/api/stop", headers=HEADERS)


def test_a_simulated_feed_is_never_reported_as_real_market_data(
    isolated_env, monkeypatch
) -> None:
    client = _client(monkeypatch)
    body = client.post("/api/start", json=SIMULATED, headers=HEADERS).json()
    assert body["real_market_data"] is False
    client.post("/api/stop", headers=HEADERS)


def test_a_stopped_desk_reports_no_trades_and_no_pnl(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    body = client.get("/api/status").json()
    assert body["trades"] == 0
    assert float(body["today_pnl"]) == 0.0
    assert float(body["open_pnl"]) == 0.0
    assert body["current_decision"] == "HOLD"


# ==========================================================================
# Nothing here reaches real money
# ==========================================================================
def test_no_route_can_reach_live_trading(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    assert LIVE_TRADING_ALLOWED is False
    for path in ("/health", "/", "/api/status", "/api/agent", "/api/system"):
        body = client.get(path).json()
        if "live" in body:
            assert body["live"] is False, path

    # There is no payload field that turns the desk live.
    body = client.post(
        "/api/start",
        json={**SIMULATED, "live": True, "mode": "live", "real_money": True},
        headers=HEADERS,
    ).json()
    assert body["live"] is False
    assert body.get("live_trading_allowed", False) is False
    assert body.get("broker") == "NOT USED"
    client.post("/api/stop", headers=HEADERS)


def test_the_service_does_not_publish_its_own_api_surface(isolated_env, monkeypatch) -> None:
    """No docs, no schema. The worker is infrastructure, not a public API."""
    client = _client(monkeypatch)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_the_worker_does_not_hand_the_browser_a_cross_origin_key(
    isolated_env, monkeypatch
) -> None:
    """No CORS on control routes.

    The token has to stay on the server, so the browser must never call Start
    on the worker directly. /health is allowed a * so a sleeping free host can
    be woken without going through Vercel.
    """
    client = _client(monkeypatch)
    response = client.get("/api/status", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    health = client.get("/health", headers={"Origin": "https://ai-trader-snowy.vercel.app"})
    assert health.headers.get("access-control-allow-origin") == "*"


def test_snapshot_is_paper_only_and_not_a_trade(isolated_env, monkeypatch) -> None:
    client = _client(monkeypatch)
    body = client.get("/api/snapshot").json()
    assert body["live"] is False
    assert body["live_trading_allowed"] is False
    assert body["engine"] == "python-worker"
    assert isinstance(body.get("sql"), str)
