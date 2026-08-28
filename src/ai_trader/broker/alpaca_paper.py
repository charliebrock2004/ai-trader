"""Alpaca paper-trading adapter (stub).

Will later use the Alpaca paper REST API only:
    https://paper-api.alpaca.markets

This module:
- never imports an Alpaca SDK
- never opens a network connection
- rejects any non-paper base URL
- refuses to place orders
"""

from __future__ import annotations

from typing import Any

from ai_trader.broker.base import Broker
from ai_trader.config import Settings
from ai_trader.exceptions import BrokerNotEnabledError, LiveTradingBlockedError
from ai_trader.safety import ALPACA_PAPER_BASE_URL, assert_broker_url_safe
from ai_trader.types import IntendedOrder, RiskVerdict


class AlpacaPaperBroker(Broker):
    name = "alpaca_paper"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        url = assert_broker_url_safe(
            settings.alpaca_base_url, mode=settings.trading_mode
        )
        if url and url.rstrip("/") != ALPACA_PAPER_BASE_URL.rstrip("/"):
            raise LiveTradingBlockedError("Alpaca adapter only accepts the paper URL.")
        self.base_url = ALPACA_PAPER_BASE_URL
        self._connected = False

    def connect(self) -> None:
        raise BrokerNotEnabledError(
            "Alpaca paper trading is not connected in the foundation build. "
            "No HTTP requests are made."
        )

    def submit(self, order: IntendedOrder, verdict: RiskVerdict) -> dict[str, Any]:
        raise BrokerNotEnabledError(
            "Alpaca order placement is disabled in the foundation build."
        )

    def account(self) -> dict[str, Any]:
        return {
            "available": False,
            "source": self.name,
            "base_url": self.base_url,
            "configured": self.settings.alpaca_configured(),
            "connected": False,
        }

    def health(self) -> dict:
        return {
            "name": self.name,
            "connected": False,
            "configured": self.settings.alpaca_configured(),
            "orders_enabled": False,
            "base_url": self.base_url,
            "notes": (
                "Future adapter will authenticate against the Alpaca paper API only. "
                "The live API host is rejected by the safety module."
            ),
        }
