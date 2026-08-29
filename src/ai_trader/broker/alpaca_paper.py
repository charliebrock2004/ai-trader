"""Alpaca PAPER adapter.

Talks only to the paper REST host. Never live. Never real money.
Secrets are not logged. Injected HTTP client is used in tests.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from ai_trader.broker.base import Broker, assert_may_submit
from ai_trader.exceptions import (
    AlpacaPaperUnavailableError,
    KillSwitchEngagedError,
    LiveTradingBlockedError,
    OrderPlacementDisabledError,
)
from ai_trader.config import Settings
from ai_trader.safety import (
    ALPACA_PAPER_BASE_URL,
    LIVE_TRADING_ALLOWED,
    assert_broker_url_safe,
    assert_live_trading_disabled,
    is_alpaca_live_url,
)
from ai_trader.types import IntendedOrder, RiskVerdict

PAPER_HOST = "paper-api.alpaca.markets"
DEFAULT_TIMEOUT = 10.0


def alpaca_paper_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper().replace("-", "/")
    if raw in {"BTC/USD", "ETH/USD", "BTCUSD", "ETHUSD"}:
        return "BTC/USD" if raw.startswith("BTC") else "ETH/USD"
    return raw.replace("_", "/")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AlpacaPaperBroker(Broker):
    name = "alpaca_paper"

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        assert_live_trading_disabled()
        if LIVE_TRADING_ALLOWED:
            raise LiveTradingBlockedError("Live trading is disabled.")
        url = assert_broker_url_safe(
            settings.alpaca_base_url, mode=settings.trading_mode
        )
        if url and is_alpaca_live_url(url):
            raise LiveTradingBlockedError("Alpaca live API URL is blocked.")
        self.settings = settings
        self.base_url = ALPACA_PAPER_BASE_URL
        self._assert_safe_url(self.base_url)
        self.timeout = float(timeout)
        self._http = http_client
        self._connected = False
        self.submit_calls = 0
        self.http_calls: list[dict[str, Any]] = []

    def _assert_safe_url(self, url: str) -> None:
        if is_alpaca_live_url(url):
            raise LiveTradingBlockedError("Alpaca live API URL is blocked.")
        host = (urlparse(url.strip()).hostname or "").lower()
        if host != PAPER_HOST:
            raise LiveTradingBlockedError("Alpaca adapter only accepts the paper host.")

    def _auth_headers(self) -> dict[str, str]:
        if not self.settings.alpaca_configured():
            raise AlpacaPaperUnavailableError(
                "Alpaca paper credentials are missing.", failure="not_configured"
            )
        key = self.settings.alpaca_api_key.get_secret_value().strip()  # type: ignore[union-attr]
        secret = self.settings.alpaca_secret_key.get_secret_value().strip()  # type: ignore[union-attr]
        return {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _send(self, method: str, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        assert_live_trading_disabled()
        if LIVE_TRADING_ALLOWED:
            raise LiveTradingBlockedError("Live trading is disabled.")
        url = f"{self.base_url.rstrip('/')}{path}"
        self._assert_safe_url(url)
        headers = self._auth_headers()
        record = {
            "method": method.upper(),
            "url": url,
            "path": path,
            "has_tools": False,
            "body_keys": sorted(body.keys()) if body else [],
        }
        self.http_calls.append(record)
        try:
            client = self._http
            if client is None:
                import httpx

                with httpx.Client(timeout=self.timeout) as owned:
                    response = owned.request(method.upper(), url, headers=headers, json=body)
                    status = response.status_code
                    payload = response.json() if response.content else {}
            else:
                if hasattr(client, "request"):
                    response = client.request(
                        method.upper(), url, headers=headers, json=body, timeout=self.timeout
                    )
                elif method.upper() == "GET":
                    response = client.get(url, headers=headers, timeout=self.timeout)
                else:
                    response = client.post(
                        url, headers=headers, json=body, timeout=self.timeout
                    )
                status = getattr(response, "status_code", 200)
                if hasattr(response, "json"):
                    payload = response.json()
                else:
                    payload = response
            record["status"] = status
        except (LiveTradingBlockedError, AlpacaPaperUnavailableError, KillSwitchEngagedError):
            raise
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            text = str(exc).lower()
            if "timeout" in name.lower() or "timeout" in text:
                raise AlpacaPaperUnavailableError(
                    "Alpaca paper request timed out.", failure="timeout"
                ) from exc
            raise AlpacaPaperUnavailableError(
                f"Alpaca paper unavailable ({name}).", failure="network"
            ) from exc
        return self._handle_status(status, payload)

    def _handle_status(self, status: int, payload: Any) -> Any:
        if status == 401:
            raise AlpacaPaperUnavailableError(
                "Alpaca paper credentials were rejected.", failure="invalid_credentials"
            )
        if status == 403:
            raise AlpacaPaperUnavailableError(
                "Alpaca paper rejected the request.", failure="rejected"
            )
        if status in {400, 422}:
            message = "Alpaca paper order was rejected."
            if isinstance(payload, dict) and payload.get("message"):
                message = str(payload.get("message"))[:200]
            raise AlpacaPaperUnavailableError(message, failure="rejected")
        if status >= 400:
            raise AlpacaPaperUnavailableError(
                f"Alpaca paper HTTP {status}.", failure="network"
            )
        return payload

    def connect(self) -> dict[str, Any]:
        snapshot = self.account()
        if not snapshot.get("available"):
            raise AlpacaPaperUnavailableError(
                snapshot.get("reason") or "Alpaca paper account is unavailable.",
                failure=str(snapshot.get("failure") or "unavailable"),
            )
        self._connected = True
        return snapshot

    def account(self) -> dict[str, Any]:
        if not self.settings.alpaca_configured():
            return {
                "available": False,
                "source": self.name,
                "base_url": self.base_url,
                "configured": False,
                "connected": False,
                "live": False,
                "failure": "not_configured",
                "reason": "Alpaca paper credentials are missing.",
            }
        try:
            raw = self._send("GET", "/v2/account")
        except AlpacaPaperUnavailableError as exc:
            self._connected = False
            return {
                "available": False,
                "source": self.name,
                "base_url": self.base_url,
                "configured": True,
                "connected": False,
                "live": False,
                "failure": exc.failure,
                "reason": str(exc),
            }
        self._connected = True
        equity = _as_float(raw.get("equity") if isinstance(raw, dict) else 0)
        last_equity = _as_float(
            raw.get("last_equity") if isinstance(raw, dict) else equity, equity
        )
        cash = _as_float(raw.get("cash") if isinstance(raw, dict) else 0)
        return {
            "available": True,
            "source": self.name,
            "base_url": self.base_url,
            "configured": True,
            "connected": True,
            "live": False,
            "currency": (raw.get("currency") if isinstance(raw, dict) else None) or "USD",
            "cash": cash,
            "buying_power": _as_float(raw.get("buying_power") if isinstance(raw, dict) else 0),
            "account_equity": equity,
            "equity": equity,
            "portfolio_value": _as_float(
                raw.get("portfolio_value") if isinstance(raw, dict) else equity, equity
            ),
            "today_pnl": round(equity - last_equity, 2),
            "status": raw.get("status") if isinstance(raw, dict) else None,
            "pattern_day_trader": bool(
                raw.get("pattern_day_trader") if isinstance(raw, dict) else False
            ),
        }

    def positions(self) -> list[dict[str, Any]]:
        if not self.settings.alpaca_configured():
            return []
        raw = self._send("GET", "/v2/positions")
        rows = raw if isinstance(raw, list) else []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "symbol": row.get("symbol"),
                    "side": (row.get("side") or "long").upper(),
                    "quantity": _as_float(row.get("qty")),
                    "average_entry": _as_float(row.get("avg_entry_price")),
                    "current_price": _as_float(row.get("current_price")),
                    "market_value": _as_float(row.get("market_value")),
                    "unrealised_pnl": _as_float(row.get("unrealized_pl")),
                }
            )
        return out

    def submit(
        self,
        order: IntendedOrder,
        verdict: RiskVerdict,
        *,
        kill_switch: bool = False,
    ) -> dict[str, Any]:
        self.submit_calls += 1
        assert_live_trading_disabled()
        if LIVE_TRADING_ALLOWED:
            raise LiveTradingBlockedError("Live trading is disabled.")
        self._assert_safe_url(self.base_url)
        if kill_switch:
            raise KillSwitchEngagedError("Kill switch engaged. Alpaca paper order blocked.")
        assert_may_submit(verdict)
        if order.qty <= 0:
            raise OrderPlacementDisabledError("Alpaca paper order quantity is zero.")
        body = {
            "symbol": alpaca_paper_symbol(order.symbol),
            "qty": str(order.qty),
            "side": order.side.value.lower() if hasattr(order.side, "value") else str(order.side).lower(),
            "type": "market",
            "time_in_force": "gtc" if "/" in alpaca_paper_symbol(order.symbol) else "day",
        }
        placed = self._send("POST", "/v2/orders", body)
        if not isinstance(placed, dict):
            placed = {"raw": placed}
        return {
            "ok": True,
            "live": False,
            "broker": self.name,
            "base_url": self.base_url,
            "id": placed.get("id"),
            "symbol": placed.get("symbol") or body["symbol"],
            "side": placed.get("side") or body["side"],
            "qty": placed.get("qty") or body["qty"],
            "status": placed.get("status") or "submitted",
            "submitted_at": placed.get("submitted_at"),
        }

    def health(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "configured": self.settings.alpaca_configured(),
            "orders_enabled": False,
            "paper_orders": self.settings.alpaca_configured(),
            "base_url": self.base_url,
            "live": False,
            "live_trading_allowed": False,
            "notes": (
                "Alpaca PAPER adapter. Host locked to paper-api. "
                "Live trading is disabled. Secrets are not logged."
            ),
        }
