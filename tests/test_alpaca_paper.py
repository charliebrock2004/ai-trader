from __future__ import annotations

from urllib.parse import urlparse

import pytest

from ai_trader.broker.alpaca_paper import AlpacaPaperBroker, alpaca_paper_symbol
from ai_trader.config import Settings, clear_settings_cache
from ai_trader.db.repository import Repository
from ai_trader.exceptions import (
    AlpacaPaperUnavailableError,
    KillSwitchEngagedError,
    LiveTradingBlockedError,
    OrderPlacementDisabledError,
)
from ai_trader.kill_switch import KillSwitch
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.risk.engine import RiskEngine
from ai_trader.safety import ALPACA_LIVE_BASE_URL, ALPACA_PAPER_BASE_URL, LIVE_TRADING_ALLOWED, is_alpaca_live_url
from ai_trader.types import IntendedOrder, RiskVerdict, Side


ACCOUNT = {
    "id": "paper-account",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "100000",
    "equity": "100000",
    "last_equity": "99900",
    "buying_power": "200000",
    "portfolio_value": "100000",
    "pattern_day_trader": False,
}

ORDER = {
    "id": "ord-paper-1",
    "status": "accepted",
    "symbol": "BTC/USD",
    "qty": "0.001",
    "side": "buy",
    "submitted_at": "2026-08-28T14:00:00Z",
}


class FakeResponse:
    def __init__(self, payload, status=200) -> None:
        self._payload = payload
        self.status_code = status
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


class FakeAlpacaHTTP:
    def __init__(self, routes=None, error=None) -> None:
        self.routes = routes or {}
        self.error = error
        self.calls: list[dict] = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {"method": str(method).upper(), "url": url, "headers": headers or {}, "json": json}
        )
        if self.error:
            raise self.error
        path = urlparse(url).path
        spec = self.routes.get(f"{str(method).upper()} {path}") or self.routes.get(path)
        if spec is None:
            return FakeResponse({"message": "not found"}, status=404)
        status, payload = spec
        return FakeResponse(payload, status=status)

    def get(self, url, headers=None, timeout=None):
        return self.request("GET", url, headers=headers, timeout=timeout)

    def post(self, url, headers=None, json=None, timeout=None):
        return self.request("POST", url, headers=headers, json=json, timeout=timeout)


def _paper_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST-not-real")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret-not-real")
    monkeypatch.setenv("ALPACA_BASE_URL", ALPACA_PAPER_BASE_URL)
    clear_settings_cache()
    return Settings()


def _broker(monkeypatch, routes=None, error=None) -> tuple[AlpacaPaperBroker, FakeAlpacaHTTP]:
    http = FakeAlpacaHTTP(routes=routes, error=error)
    return AlpacaPaperBroker(_paper_settings(monkeypatch), http_client=http), http


def test_symbol_mapping() -> None:
    assert alpaca_paper_symbol("BTC-USD") == "BTC/USD"
    assert alpaca_paper_symbol("ETH/USD") == "ETH/USD"


def test_authentication_and_account(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, http = _broker(
        monkeypatch,
        routes={"GET /v2/account": (200, ACCOUNT)},
    )
    snap = broker.connect()
    assert snap["available"] is True
    assert snap["account_equity"] == 100000.0
    assert snap["today_pnl"] == 100.0
    assert snap["live"] is False
    assert broker.health()["connected"] is True
    assert http.calls[0]["url"].startswith(ALPACA_PAPER_BASE_URL)
    assert "APCA-API-KEY-ID" in http.calls[0]["headers"]
    assert all("secret-not-real" not in str(c) for c in broker.http_calls)
    assert all("PKTEST" not in str(c) for c in broker.http_calls)
    assert LIVE_TRADING_ALLOWED is False


def test_positions(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(
        monkeypatch,
        routes={
            "GET /v2/account": (200, ACCOUNT),
            "GET /v2/positions": (
                200,
                [
                    {
                        "symbol": "BTC/USD",
                        "qty": "0.01",
                        "side": "long",
                        "avg_entry_price": "70000",
                        "current_price": "78757",
                        "market_value": "787.57",
                        "unrealized_pl": "87.57",
                    }
                ],
            ),
        },
    )
    rows = broker.positions()
    assert rows[0]["symbol"] == "BTC/USD"
    assert rows[0]["quantity"] == 0.01
    assert rows[0]["unrealised_pnl"] == 87.57


def test_order_goes_to_paper_only(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, http = _broker(
        monkeypatch,
        routes={
            "GET /v2/account": (200, ACCOUNT),
            "POST /v2/orders": (200, ORDER),
        },
    )
    placed = broker.submit(
        IntendedOrder(symbol="BTC-USD", side=Side.BUY, qty=0.001),
        RiskVerdict(approved=True, reason="sized"),
    )
    assert placed["ok"] is True
    assert placed["live"] is False
    post = [c for c in http.calls if c["method"] == "POST"][0]
    assert post["url"] == f"{ALPACA_PAPER_BASE_URL}/v2/orders"
    assert is_alpaca_live_url(post["url"]) is False
    host = urlparse(post["url"]).hostname
    assert host == "paper-api.alpaca.markets"
    assert post["json"]["symbol"] == "BTC/USD"
    assert post["json"]["side"] == "buy"


def test_unapproved_order_never_hits_network(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, http = _broker(monkeypatch, routes={"GET /v2/account": (200, ACCOUNT)})
    with pytest.raises(OrderPlacementDisabledError):
        broker.submit(
            IntendedOrder(symbol="BTC-USD", side=Side.BUY, qty=0.001),
            RiskVerdict(approved=False, reason="risk"),
        )
    assert http.calls == []


def test_order_rejection(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(
        monkeypatch,
        routes={"POST /v2/orders": (422, {"message": "insufficient qty"})},
    )
    with pytest.raises(AlpacaPaperUnavailableError) as exc:
        broker.submit(
            IntendedOrder(symbol="BTC-USD", side=Side.BUY, qty=0.001),
            RiskVerdict(approved=True, reason="ok"),
        )
    assert exc.value.failure == "rejected"


def test_network_failure(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(monkeypatch, error=ConnectionError("down"))
    snap = broker.account()
    assert snap["available"] is False
    assert snap["failure"] == "network"


def test_timeout(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(monkeypatch, error=TimeoutError("timed out"))
    snap = broker.account()
    assert snap["failure"] == "timeout"


def test_invalid_credentials(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(
        monkeypatch, routes={"GET /v2/account": (401, {"message": "unauthorized"})}
    )
    snap = broker.account()
    assert snap["available"] is False
    assert snap["failure"] == "invalid_credentials"
    with pytest.raises(AlpacaPaperUnavailableError):
        broker.connect()


def test_kill_switch_blocks_submit(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, http = _broker(monkeypatch, routes={"POST /v2/orders": (200, ORDER)})
    with pytest.raises(KillSwitchEngagedError):
        broker.submit(
            IntendedOrder(symbol="BTC-USD", side=Side.BUY, qty=0.001),
            RiskVerdict(approved=True, reason="ok"),
            kill_switch=True,
        )
    assert http.calls == []


def test_risk_rejection_in_pipeline(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    risk = RiskEngine(allow_orders=False)
    verdict = risk.review_paper(
        "BUY",
        price=78757,
        account={"account_equity": 100.0, "cash": 100.0, "day_start_equity": 100.0},
        open_positions=2,
        trades_today=0,
        daily_pnl=0,
        has_position=False,
        halted=False,
        kill_switch=False,
    )
    assert verdict.approved is False
    broker, http = _broker(monkeypatch, routes={"POST /v2/orders": (200, ORDER)})
    with pytest.raises(OrderPlacementDisabledError):
        broker.submit(
            IntendedOrder(symbol="BTC-USD", side=Side.BUY, qty=1),
            RiskVerdict(approved=False, reason=verdict.reason),
        )
    assert http.calls == []


def test_live_url_is_impossible(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(monkeypatch)
    assert is_alpaca_live_url(broker.base_url) is False
    with pytest.raises(LiveTradingBlockedError):
        broker._assert_safe_url(ALPACA_LIVE_BASE_URL)
    with pytest.raises(LiveTradingBlockedError):
        broker._assert_safe_url("https://api.alpaca.markets/v2/orders")
    assert LIVE_TRADING_ALLOWED is False
    assert "paper-api.alpaca.markets" in broker.base_url


def test_live_trading_remains_disabled(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    broker, _http = _broker(monkeypatch)
    assert LIVE_TRADING_ALLOWED is False
    assert broker.health()["live"] is False
    assert broker.health()["orders_enabled"] is False
    assert broker.health()["live_trading_allowed"] is False


def test_orchestrator_alpaca_hold_does_not_submit(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _paper_settings(monkeypatch)
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)
    http = FakeAlpacaHTTP(
        routes={
            "GET /v2/account": (200, ACCOUNT),
            "GET /v2/positions": (200, []),
            "POST /v2/orders": (200, ORDER),
        }
    )
    orch.alpaca_broker = AlpacaPaperBroker(settings, http_client=http)
    result = orch.start_paper_session(symbol="SIM-UP", bars=16, warmup=8, grok_frequency=8)
    assert result["live"] is False
    # The internal simulator is the only fill path. Alpaca is observation only.
    assert result["execution"] == "simulated"
    assert result["broker"] == "NOT USED"
    assert result["decision"] == "HOLD"
    assert not any(c["method"] == "POST" for c in http.calls)
    repo.close()


def test_orchestrator_kill_switch_blocks_alpaca(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _paper_settings(monkeypatch)
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    orch = Orchestrator(settings, repo, switch)
    http = FakeAlpacaHTTP(
        routes={
            "GET /v2/account": (200, ACCOUNT),
            "GET /v2/positions": (200, []),
            "POST /v2/orders": (200, ORDER),
        }
    )
    orch.alpaca_broker = AlpacaPaperBroker(settings, http_client=http)
    result = orch.start_paper_session(symbol="SIM-UP", bars=12, warmup=4, grok_frequency=8)
    assert result["broker"] == "NOT USED"
    assert not any(c["method"] == "POST" for c in http.calls)
    assert result["live"] is False
    repo.close()


def test_orchestrator_never_submits_a_broker_order_even_on_buy(
    isolated_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BUY decision must not reach any broker from the session path.

    This used to submit an Alpaca paper order through a second, weaker risk
    check whose fills never touched PaperLedger. The internal simulator is now
    the only fill path.
    """
    settings = _paper_settings(monkeypatch)
    repo = Repository(settings.resolve_database_path())
    switch = KillSwitch(settings.resolve_kill_switch_path(), initially_engaged=True)
    switch.disengage("test")
    orch = Orchestrator(settings, repo, switch)
    http = FakeAlpacaHTTP(
        routes={
            "GET /v2/account": (200, ACCOUNT),
            "GET /v2/positions": (200, []),
            "POST /v2/orders": (200, ORDER),
        }
    )
    orch.alpaca_broker = AlpacaPaperBroker(settings, http_client=http)
    report = {
        "ok": True,
        "live": False,
        "symbol": "BTC-USD",
        "last_price": 78757.0,
        "ai_decisions": [{"action": "BUY", "bar": 16, "reasoning": "test"}],
        "current_decision": "BUY",
        "trades": 0,
        "orders": [],
        "fills": [],
        "closed": [],
        "performance": {},
        "position": "flat",
        "balance": 100.0,
    }
    out = orch._attach_alpaca_paper(report)
    assert [c for c in http.calls if c["method"] == "POST"] == []
    assert out["broker"] == "NOT USED"
    assert out["execution"] == "simulated"
    assert out["alpaca_submit_calls"] == 0
    assert out["broker_submit_calls"] == 0
    assert out["live"] is False
    # The balance shown stays the internal paper book, not the Alpaca mirror.
    assert out["balance"] == 100.0
    assert out["alpaca_account"]["account_equity"] != 100.0
    repo.close()
