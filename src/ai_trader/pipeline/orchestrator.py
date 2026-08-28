"""Pipeline wiring.

Market data → analysis → Grok → decision → risk → broker → database

The orchestrator is the only thing allowed to call those modules in sequence.
During the foundation build it performs a dry run: safety checks, logging,
and a heartbeat event. It never places an order.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.analysis.base import NullAnalyst
from ai_trader.broker.alpaca_paper import AlpacaPaperBroker
from ai_trader.broker.simulated import SimulatedBroker
from ai_trader.config import Settings
from ai_trader.db.repository import Repository
from ai_trader.exceptions import (
    FoundationModeError,
    KillSwitchEngagedError,
    OrderPlacementDisabledError,
)
from ai_trader.kill_switch import KillSwitch
from ai_trader.logging_setup import get_logger
from ai_trader.market_data.simulated import SimulatedMarketData
from ai_trader.risk.engine import RiskEngine
from ai_trader.safety import assert_safe_to_run, safety_report

log = get_logger("pipeline")

PIPELINE_STAGES = (
    "market_data",
    "analysis",
    "ai",
    "decision",
    "risk",
    "execution",
    "database",
)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        kill_switch: KillSwitch,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.kill_switch = kill_switch
        self.market_data = SimulatedMarketData()
        self.analysis = NullAnalyst()
        self.ai = GrokAnalyst(settings)
        self.risk = RiskEngine(allow_orders=False)
        self.simulated_broker = SimulatedBroker()
        self.alpaca_broker = AlpacaPaperBroker(settings)
        self.broker = self.simulated_broker

    def _select_broker_name(self) -> str:
        if self.settings.trading_mode == "paper":
            return self.alpaca_broker.name
        return self.simulated_broker.name

    def architecture(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "market_data",
                "title": "Market Data",
                "status": "stub",
                "detail": self.market_data.health()["notes"],
            },
            {
                "id": "analysis",
                "title": "Market / News Analysis",
                "status": "stub",
                "detail": self.analysis.health()["notes"],
            },
            {
                "id": "ai",
                "title": "Grok AI",
                "status": "gated",
                "detail": self.ai.health()["notes"],
            },
            {
                "id": "decision",
                "title": "BUY / SELL / HOLD",
                "status": "idle",
                "detail": "Decisions are proposals only. None have been produced.",
            },
            {
                "id": "risk",
                "title": "Risk Engine",
                "status": "active",
                "detail": "Hard gate. Currently rejects every order.",
            },
            {
                "id": "execution",
                "title": "Paper Execution",
                "status": "blocked",
                "detail": "Order placement is disabled. Broker adapters are stubs.",
            },
            {
                "id": "database",
                "title": "Trade & Event Log",
                "status": "active",
                "detail": "SQLite is initialised and recording system events.",
            },
        ]

    def status(self) -> dict[str, Any]:
        safety = safety_report(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        return {
            "foundation": True,
            "orders_enabled": False,
            "trading_mode": self.settings.trading_mode,
            "broker": self._select_broker_name(),
            "kill_switch": self.kill_switch.snapshot(),
            "safety": safety,
            "config": self.settings.public_view(),
            "modules": {
                "market_data": self.market_data.health(),
                "analysis": self.analysis.health(),
                "ai": self.ai.health(),
                "risk": self.risk.health(),
                "broker_simulated": self.simulated_broker.health(),
                "broker_alpaca": self.alpaca_broker.health(),
                "database": self.repository.health(),
            },
            "architecture": self.architecture(),
        }

    def dry_run(self, symbols: Optional[list[str]] = None) -> dict[str, Any]:
        """Heartbeat through the pipeline without calling Grok or any broker."""
        assert_safe_to_run(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        try:
            self.kill_switch.assert_clear()
        except KillSwitchEngagedError as exc:
            self.repository.record_event(
                level="WARNING",
                source="pipeline",
                event_type="dry_run_blocked",
                message=str(exc),
            )
            return {"ok": False, "blocked_by": "kill_switch", "message": str(exc)}

        symbols = symbols or ["SPY"]
        snapshot = self.market_data.snapshot(symbols)
        analysis_error = None
        try:
            self.analysis.analyse(snapshot)
        except FoundationModeError as exc:
            analysis_error = str(exc)

        ai_error = None
        try:
            self.ai.propose(snapshot)
        except FoundationModeError as exc:
            ai_error = str(exc)

        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="dry_run",
            message="Foundation dry run completed. No orders placed.",
            details={
                "symbols": symbols,
                "snapshot_source": snapshot.source,
                "analysis": analysis_error,
                "ai": ai_error,
                "orders_enabled": False,
            },
        )
        log.info("Dry run complete for %s — no orders placed", symbols)
        return {
            "ok": True,
            "orders_placed": 0,
            "symbols": symbols,
            "analysis": analysis_error,
            "ai": ai_error,
        }

    def place_order(self, *args: Any, **kwargs: Any) -> None:
        raise OrderPlacementDisabledError(
            "The orchestrator will not place orders in the foundation build."
        )
