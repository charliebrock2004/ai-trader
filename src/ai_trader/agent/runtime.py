"""The agent's durable runtime.

Everything the agent is — its capital, its survival state, its open positions
and whether it is dead — lives in the database, not in this object. This class
rebuilds itself from that state on every start, which is what makes a worker
restart a non-event and makes a TERMINAL agent stay terminal.

Startup refuses to proceed if the agent is terminated. That check runs before
anything else is constructed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ai_trader.agent.cycle import AgentCycle, CycleReport
from ai_trader.analytics.performance import compute_performance
from ai_trader.clock import Clock, default_clock
from ai_trader.contracts.ledger import ContractLedger
from ai_trader.contracts.risk import ContractRiskEngine, ContractRiskLimits
from ai_trader.costs.ledger import CostLedger
from ai_trader.db.records import RecordStore
from ai_trader.edge.opportunity import OpportunityEngine, OpportunityFilters
from ai_trader.markets.paper import PaperPredictionMarket
from ai_trader.survival.config import SurvivalConfig
from ai_trader.survival.engine import MILESTONES, SurvivalEngine
from ai_trader.survival.latch import AgentTerminatedError, TerminalLatch
from ai_trader.survival.policy import PolicyGuardian


class AgentRuntime:
    """Owns the agent's long-lived state and rebuilds it from the database."""

    def __init__(
        self,
        *,
        store: RecordStore,
        data_dir: Path,
        survival_config: Optional[SurvivalConfig] = None,
        clock: Optional[Clock] = None,
        event_source: Any = None,
        market: Any = None,
        analyst: Any = None,
        fx_rate: float = 1.0,
        quote_currency: str = "USD",
        hosting_per_day: float = 0.0,
        filters: Optional[OpportunityFilters] = None,
        risk_limits: Optional[ContractRiskLimits] = None,
        require_analyst: bool = True,
        grok_budget: Any = None,
    ) -> None:
        self.require_analyst = require_analyst
        self.grok_budget = grok_budget
        self.clock = clock or default_clock()
        self.store = store
        self.data_dir = Path(data_dir)
        self.config = survival_config or SurvivalConfig()

        self.latch = TerminalLatch(
            self.data_dir / "TERMINAL", store=store, clock=self.clock
        )
        self.survival = SurvivalEngine(
            self.config, latch=self.latch, store=store, clock=self.clock
        )
        self.guardian = PolicyGuardian(self.survival)
        self.ledger = ContractLedger(
            starting_cash=self.config.starting_equity,
            base_currency=self.config.base_currency,
        )
        self.ledger.set_fx(quote_currency, fx_rate)
        self.risk = ContractRiskEngine(risk_limits or ContractRiskLimits())
        self.costs = CostLedger(
            store,
            clock=self.clock,
            base_currency=self.config.base_currency,
            hosting_per_day=hosting_per_day,
            fx_usd_to_base=fx_rate if quote_currency == "USD" else 1.0,
        )
        self.market = market or PaperPredictionMarket(clock=self.clock)
        self.event_source = event_source
        self.analyst = analyst
        self.fx_rate = float(fx_rate)
        self.quote_currency = quote_currency
        self.filters = filters or OpportunityFilters()
        self.last_cycle: Optional[dict[str, Any]] = None
        self.last_error: Optional[str] = None

        self._restore()

    # ------------------------------------------------------------------
    def _restore(self) -> None:
        """Rebuild cash and open positions from what was persisted.

        The ledger starts at the configured opening balance and then replays
        the recorded position history, so the in-memory book always matches
        the audit trail rather than drifting from it.
        """
        rows = self.store.list_contract_positions(limit=1000)
        realised = 0.0
        for row in sorted(rows, key=lambda r: r["id"]):
            premium = float(row["premium_base"])
            fees = float(row["fees_base"] or 0.0)
            if row["open"]:
                from ai_trader.contracts.ledger import ContractPosition

                position = ContractPosition(
                    position_id=row["position_id"],
                    ticker=row["ticker"],
                    event_key=row["event_key"] or "",
                    side=row["side"],
                    contracts=int(row["contracts"]),
                    average_price=float(row["average_price"]),
                    premium_base=premium,
                    fees_base=fees,
                    entry_fx=self.fx_rate,
                    quote_currency=self.quote_currency,
                    opened_at=row["created_at"],
                    decision_id=row["decision_id"],
                )
                self.ledger.positions[row["ticker"]] = position
                self.ledger.cash -= premium + fees
            else:
                pnl = row["realised_pnl_base"]
                if pnl is not None:
                    realised += float(pnl)
        self.ledger.cash = round(self.ledger.cash + realised, 2)
        self.ledger.realised_pnl = round(realised, 2)
        self.survival.observe(self.ledger.equity(), reason="runtime restore")

    # ------------------------------------------------------------------
    def assert_alive(self) -> None:
        """Refuse to run a terminated agent. Called before any cycle."""
        self.survival.assert_alive()

    def run_cycle(self) -> dict[str, Any]:
        try:
            self.assert_alive()
        except AgentTerminatedError as exc:
            self.last_error = str(exc)
            return {
                "ok": False,
                "terminated": True,
                "error": str(exc),
                "survival": self.survival.snapshot(equity=self.ledger.equity()),
            }
        if self.event_source is None:
            self.last_error = "No event source configured."
            return {
                "ok": False,
                "error": self.last_error,
                "survival": self.survival.snapshot(equity=self.ledger.equity()),
            }

        cycle = AgentCycle(
            event_source=self.event_source,
            market=self.market,
            ledger=self.ledger,
            risk=self.risk,
            guardian=self.guardian,
            survival=self.survival,
            store=self.store,
            opportunities=OpportunityEngine(filters=self.filters),
            analyst=self.analyst,
            cost_ledger=self.costs,
            clock=self.clock,
            fx_rate=self.fx_rate,
            quote_currency=self.quote_currency,
            require_analyst=self.require_analyst,
            grok_budget=self.grok_budget,
        )
        report: CycleReport = cycle.run()
        self.last_cycle = report.to_dict()
        self.last_error = report.errors[0] if report.errors else None
        return {"ok": True, **self.last_cycle}

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Everything the home screen needs, all of it from real state."""
        equity = self.ledger.equity()
        survival = self.survival.snapshot(equity=equity)
        life = self.store.agent_life() or {}
        costs = self.costs.summary(
            equity=equity,
            starting_equity=self.config.starting_equity,
            terminal_threshold=self.config.terminal_equity,
            realised_pnl=self.ledger.realised_pnl,
        )
        counts = self.store.decision_counts()
        last_decision = (self.store.list_decisions(limit=1) or [None])[0]
        return {
            "ok": True,
            "live": False,
            "broker": "NOT USED",
            "banner": "PAPER SIMULATION — NO REAL TRADING",
            "alive": not survival["terminated"],
            "terminated": survival["terminated"],
            "survival": survival,
            "born_at": life.get("born_at"),
            "age_days": self._age_days(life.get("born_at")),
            "account": self.ledger.snapshot(),
            "costs": costs,
            "milestones": self.store.list_milestones(),
            "next_milestone": self._next_milestone(equity),
            "decisions": counts,
            "last_decision": last_decision,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
            "open_positions": [p.to_dict() for p in self.ledger.open_positions()],
            "config": {
                "starting_equity": self.config.starting_equity,
                "base_currency": self.config.base_currency,
                "terminal_threshold": self.config.terminal_equity,
                "filters": self.filters.to_dict(),
                "risk_limits": self.risk.limits.to_dict(),
            },
        }

    def performance(self) -> dict[str, Any]:
        return compute_performance(
            self.store,
            starting_equity=self.config.starting_equity,
            equity=self.ledger.equity(),
            terminal_threshold=self.config.terminal_equity,
            cost_ledger=self.costs,
        )

    def system(self) -> dict[str, Any]:
        """Component health. Reports broken components as broken."""
        equity = self.ledger.equity()
        survival = self.survival.snapshot(equity=equity)
        event_health = self._health_of(
            self.event_source, "No event source configured."
        )
        analyst_health = self._health_of(
            self.analyst,
            "No analyst configured. Nothing challenges the deterministic edge, so "
            "every opportunity is held."
            if self.require_analyst
            else "No analyst configured, and reviews are not required. Trades run "
            "unchallenged.",
        )
        components = [
            {
                "id": "agent",
                "title": "Agent",
                "ok": not survival["terminated"],
                "detail": (
                    "TERMINATED — permanently shut down."
                    if survival["terminated"]
                    else f"Alive in {survival['state']}."
                ),
            },
            {
                "id": "survival",
                "title": "Survival engine",
                "ok": True,
                "detail": (
                    f"{survival['state']} · {survival['life_remaining_pct']}% of the way "
                    f"from the terminal threshold to the starting stake."
                ),
            },
            {
                "id": "terminal_latch",
                "title": "Terminal latch",
                "ok": not survival["terminated"],
                "detail": survival["latch"].get("reason", "Not engaged."),
            },
            {
                "id": "events",
                "title": f"Event source ({event_health.get('name')})",
                "ok": bool(event_health.get("ready")),
                "detail": event_health.get("notes", ""),
            },
            {
                "id": "market",
                "title": "Prediction market",
                "ok": bool(self.market.health().get("ready")),
                "detail": self.market.health().get("notes", ""),
            },
            {
                "id": "analyst",
                "title": f"Analyst ({analyst_health.get('name')})",
                "ok": bool(analyst_health.get("ready")),
                "detail": analyst_health.get("notes", ""),
            },
            {
                "id": "database",
                "title": "Audit trail",
                "ok": True,
                "detail": f"{self.store.decision_counts().get('TOTAL', 0)} decisions recorded.",
            },
        ]
        return {
            "ok": all(c["ok"] for c in components),
            "live": False,
            "paper_only": True,
            "components": components,
            "last_error": self.last_error,
            "last_cycle_at": (self.last_cycle or {}).get("finished_at"),
            "survival": survival,
            "strategy": "event-driven prediction markets",
            "event_family": event_health.get("name", "none"),
        }

    @staticmethod
    def _health_of(component: Any, absent_note: str) -> dict[str, Any]:
        """Health for one component, tolerating one that cannot report it.

        The system page exists to show breakage, so it must not itself break
        when a component is missing or does not implement ``health()``.
        """
        if component is None:
            return {"name": "none", "ready": False, "notes": absent_note}
        reporter = getattr(component, "health", None)
        if not callable(reporter):
            return {
                "name": getattr(component, "name", type(component).__name__),
                "ready": False,
                "notes": "Component does not report health.",
            }
        try:
            health = reporter()
        except Exception as exc:  # noqa: BLE001 — a broken component is the news
            return {
                "name": getattr(component, "name", type(component).__name__),
                "ready": False,
                "notes": f"Health check failed: {type(exc).__name__}: {exc}",
            }
        return health if isinstance(health, dict) else {"name": "unknown", "ready": False}

    # ------------------------------------------------------------------
    def _age_days(self, born_at: Optional[str]) -> Optional[float]:
        if not born_at:
            return None
        from datetime import datetime

        from ai_trader.clock import ensure_utc

        try:
            born = ensure_utc(datetime.fromisoformat(born_at))
        except (TypeError, ValueError):
            return None
        return round((ensure_utc(self.clock.now()) - born).total_seconds() / 86_400.0, 3)

    @staticmethod
    def _next_milestone(equity: float) -> Optional[dict[str, Any]]:
        for threshold, key, label in MILESTONES:
            if equity < threshold:
                return {"key": key, "label": label, "equity": threshold}
        return None
