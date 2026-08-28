from __future__ import annotations

import pytest

from ai_trader.broker.alpaca_paper import AlpacaPaperBroker
from ai_trader.broker.simulated import SimulatedBroker
from ai_trader.config import Settings
from ai_trader.exceptions import BrokerNotEnabledError, OrderPlacementDisabledError
from ai_trader.risk.engine import RiskEngine
from ai_trader.types import Action, Decision, IntendedOrder, RiskVerdict, Side


def _decision() -> Decision:
    return Decision(
        symbol="SPY",
        action=Action.BUY,
        confidence=0.9,
        rationale="test",
        model="test",
    )


def test_risk_rejects_all_orders() -> None:
    engine = RiskEngine()
    verdict = engine.review(_decision())
    assert verdict.approved is False


def test_simulated_broker_refuses_orders() -> None:
    broker = SimulatedBroker()
    order = IntendedOrder(symbol="SPY", side=Side.BUY, qty=1)
    with pytest.raises(OrderPlacementDisabledError):
        broker.submit(order, RiskVerdict(approved=False, reason="no"))
    with pytest.raises(OrderPlacementDisabledError):
        broker.submit(order, RiskVerdict(approved=True, reason="should still fail"))


def test_alpaca_stub_does_not_connect(isolated_env: object) -> None:
    broker = AlpacaPaperBroker(Settings())
    with pytest.raises(BrokerNotEnabledError):
        broker.connect()
    with pytest.raises(BrokerNotEnabledError):
        broker.submit(
            IntendedOrder(symbol="SPY", side=Side.BUY, qty=1),
            RiskVerdict(approved=True, reason="no"),
        )
    assert broker.health()["connected"] is False
    assert broker.health()["orders_enabled"] is False
    assert "paper-api.alpaca.markets" in broker.base_url
