"""Process-wide runtime: settings, logging, database, kill switch, pipeline."""

from __future__ import annotations

import threading
from typing import Any, Optional

from ai_trader.agent.runtime import AgentRuntime
from ai_trader.agent.worker import DeskWorker
from ai_trader.ai.skeptic import GrokSkeptic
from ai_trader.clock import SystemClock
from ai_trader.config import Settings, get_settings
from ai_trader.db.repository import Repository
from ai_trader.events.bls import BLSCPISource
from ai_trader.fx.provider import FxRateUnavailableError, PublicFxFeed
from ai_trader.kill_switch import KillSwitch, get_kill_switch
from ai_trader.markets.cpi_contracts import register_cpi_ladder
from ai_trader.markets.paper import PaperPredictionMarket
from ai_trader.logging_setup import setup_logging
from ai_trader.pipeline.orchestrator import Orchestrator
from ai_trader.survival.config import SurvivalConfig


class Runtime:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        setup_logging(self.settings.resolve_log_dir(), self.settings.log_level)
        self.clock = SystemClock()
        self.repository = Repository(
            self.settings.resolve_database_path(), clock=self.clock
        )
        self.kill_switch = get_kill_switch(
            self.settings.resolve_kill_switch_path(),
            initially_engaged=self.settings.kill_switch_engaged,
        )
        self.orchestrator = Orchestrator(
            self.settings, self.repository, self.kill_switch, clock=self.clock
        )
        self.agent = self._build_agent()
        self.worker = DeskWorker(self)
        self.orchestrator.paper_session.on_persist = self.orchestrator._persist_session
        self.orchestrator.paper_session.on_decision = self._record_spot_decision
        self.worker.recover()
        self._refresh_fx_async()
        if not self._bootstrapped():
            self.repository.record_event(
                level="INFO",
                source="runtime",
                event_type="boot",
                message="AI-Trader foundation started. Order placement is disabled.",
                details={
                    "mode": self.settings.trading_mode,
                    "kill_switch": self.kill_switch.is_engaged(),
                },
            )

    def _build_agent(self) -> AgentRuntime:
        """Construct the event-driven agent from persisted state.

        Boot must not wait on a live FX fetch — Start would hang on the first
        RPC. USD→GBP is resolved in the background and on the paper-session
        thread, which fails closed if the rate is missing.
        """
        analyst = None
        if self.settings.grok_paper_analysis and self.settings.grok_configured():
            analyst = GrokSkeptic(self.settings)

        event_source = BLSCPISource(clock=self.clock, api_key=self.settings.bls_api_key)
        # Register the contract ladder so the pipeline has something concrete to
        # price. No book source is attached: with no venue adapter connected,
        # every contract reports "no order book" and the agent holds, which is
        # visible on the System page rather than looking like a quiet agent.
        market = PaperPredictionMarket(clock=self.clock)
        try:
            register_cpi_ladder(market, event_source, limit=4)
        except Exception:  # noqa: BLE001 — a calendar failure must not stop boot
            pass

        agent = AgentRuntime(
            store=self.repository.records,
            data_dir=self.settings.resolve_database_path().parent,
            survival_config=SurvivalConfig(
                starting_equity=self.settings.starting_equity,
                base_currency=self.settings.base_currency,
                terminal_at=self.settings.terminal_threshold_pct,
            ),
            clock=self.clock,
            event_source=event_source,
            market=market,
            analyst=analyst,
            fx_rate=1.0,
            quote_currency="USD",
            hosting_per_day=self.settings.hosting_cost_per_day,
        )
        agent.fx_source = "deferred"  # type: ignore[attr-defined]
        return agent

    def _refresh_fx_async(self) -> None:
        def _load() -> None:
            try:
                rate = PublicFxFeed(clock=self.clock).rate(
                    "USD", self.settings.base_currency
                )
                self.agent.fx_rate = float(rate.rate)
                self.agent.ledger.set_fx("USD", self.agent.fx_rate)
                self.agent.fx_source = rate.source  # type: ignore[attr-defined]
            except (FxRateUnavailableError, Exception):
                pass

        threading.Thread(target=_load, daemon=True, name="ai-trader-fx").start()

    def _record_spot_decision(self, payload: dict[str, Any]) -> None:
        """Write every paper-session decision, including HOLDs, into the audit trail."""
        try:
            reason = str(payload.get("reason") or payload.get("notes") or "HOLD")
            equity = payload.get("equity")
            self.repository.records.record_decision(
                {
                    "kind": "spot",
                    "ticker": payload.get("symbol"),
                    "final_action": payload.get("final_action") or "HOLD",
                    "proposed_action": payload.get("proposed_action"),
                    "policy_action": payload.get("final_action"),
                    "policy_reason": reason,
                    "notes": reason,
                    "executed": bool(payload.get("order_id")),
                    "order_ref": payload.get("order_id"),
                    "stage": payload.get("stage") or "spot",
                    "equity_before": equity,
                    "cash_before": payload.get("cash"),
                    "base_currency": self.settings.base_currency,
                    "cycle_id": f"spot:{payload.get('symbol')}:{payload.get('bar')}",
                    "risk_approved": bool(payload.get("approved")),
                }
            )
            if equity is not None:
                self.repository.records.update_agent_life(paper_equity=float(equity))
                try:
                    self.agent.survival.observe(float(equity), reason="spot decision")
                except Exception:
                    pass
        except Exception:  # noqa: BLE001 — a record failure must never kill the session
            pass

    def _bootstrapped(self) -> bool:
        counts = self.repository.table_counts()
        return counts.get("events", 0) > 0

    def close(self) -> None:
        # Process exit: halt loops, keep desired_running so recover() can
        # restart a session that the operator asked to keep running.
        try:
            self.worker.shutdown()
        except Exception:
            pass
        self.repository.close()


_RUNTIME: Optional[Runtime] = None

# Over stdio, commands arrive one at a time and a bare `if None` was enough.
# Over HTTP they arrive concurrently, and two simultaneous first requests would
# each build a Runtime — two schedulers, two sessions, two writers on one
# ledger. The lock is what keeps "one process, one agent" true.
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = Runtime()
        return _RUNTIME


def reset_runtime() -> None:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            _RUNTIME.close()
        _RUNTIME = None
