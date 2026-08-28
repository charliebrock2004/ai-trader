from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_and_home(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "AI-Trader" in home.text
    assert "Kill switch" in home.text

    status = client.get("/api/status")
    assert status.status_code == 200
    body = status.json()
    assert body["orders_enabled"] is False
    assert body["safety"]["live_trading_allowed"] is False
    assert body["kill_switch"]["engaged"] is True


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
    assert dry.json()["orders_placed"] == 0

    on = client.post(
        "/api/kill-switch",
        json={"engaged": True, "reason": "test halt"},
    )
    assert on.json()["kill_switch"]["engaged"] is True


def test_empty_ledgers(client: TestClient) -> None:
    assert client.get("/api/decisions").json()["decisions"] == []
    assert client.get("/api/trades").json()["trades"] == []
    assert client.get("/api/positions").json()["positions"] == []
