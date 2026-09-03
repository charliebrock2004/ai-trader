"""Pipeline wiring.

Market data → analysis → fixture Grok → paper account → risk → (no broker)

The orchestrator is the only thing allowed to call those modules in sequence.
It never places an order in this build.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional
import json

from ai_trader.account.simulated import SimulatedPaperAccount
from ai_trader.ai.fixture import FixtureAnalyst
from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.analysis.technical import TechnicalAnalyst, analyse_series
from ai_trader.broker.alpaca_paper import AlpacaPaperBroker
from ai_trader.broker.simulated import SimulatedBroker
from ai_trader.clock import Clock, default_clock
from ai_trader.config import Settings
from ai_trader.db.repository import Repository
from ai_trader.fx.provider import FxProvider, PublicFxFeed
from ai_trader.exceptions import (
    AlpacaPaperUnavailableError,
    KillSwitchEngagedError,
    LiveTradingBlockedError,
    OrderPlacementDisabledError,
)
from ai_trader.kill_switch import KillSwitch
from ai_trader.logging_setup import get_logger
from ai_trader.market_data.scenarios import DEFAULT_SYMBOLS
from ai_trader.market_data.public import PublicCryptoFeed
from ai_trader.market_data.simulated import SimulatedMarketData
from ai_trader.market_data.validation import validate_snapshot
from ai_trader.risk.engine import RiskEngine
from ai_trader.safety import LIVE_TRADING_ALLOWED, assert_safe_to_run, safety_report
from ai_trader.paper.signals import DEMO_SIM_UP, FixtureHoldSource, FrozenActionSource
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.market_data.generator import generate_series
from ai_trader.paper.models import PaperAction
from ai_trader.types import Action, IntendedOrder, MarketSnapshot, PaperAccountState, RiskVerdict, Side
from ai_trader.session.config import PaperSessionConfig
from ai_trader.session.runner import PaperSession

log = get_logger("pipeline")

PIPELINE_STAGES = (
    "market_data",
    "analysis",
    "ai",
    "decision",
    "paper_account",
    "risk",
    "execution",
    "database",
)

DRY_RUN_BARS = 60


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        kill_switch: KillSwitch,
        *,
        clock: Optional[Clock] = None,
        fx: Optional[FxProvider] = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.kill_switch = kill_switch
        self.clock = clock or default_clock()
        self.market_data = SimulatedMarketData()
        self.public_market_data = PublicCryptoFeed(now_fn=self.clock.now)
        # Read-only public reference rates. A USD instrument on a GBP book
        # cannot trade without one; the session fails closed if it is absent.
        self.fx = fx or PublicFxFeed(clock=self.clock)
        self.analysis = TechnicalAnalyst()
        self.ai = FixtureAnalyst()
        self.grok = GrokAnalyst(settings)
        self.paper_account = SimulatedPaperAccount()
        self.risk = RiskEngine(allow_orders=False)
        self.simulated_broker = SimulatedBroker()
        self.alpaca_broker = AlpacaPaperBroker(settings)
        self.broker = self.simulated_broker
        self.last_grok_cycle = None
        self.last_benchmark = None
        self.paper_session = PaperSession(clock=self.clock, fx=self.fx)
        self.last_session = None

    def _select_broker_name(self) -> str:
        if self.settings.trading_mode == "paper":
            return self.alpaca_broker.name
        return self.simulated_broker.name

    def _persist_account(self, state: PaperAccountState) -> int:
        payload = state.to_dict()
        return self.repository.record_account_snapshot(
            mode=self.settings.trading_mode,
            source=state.source,
            equity=state.account_equity,
            cash=state.cash,
            buying_power=state.buying_power,
            portfolio_value=state.account_equity,
            raw=payload,
        )

    def architecture(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "market_data",
                "title": "Market Data",
                "status": "active",
                "detail": self.market_data.health()["notes"],
            },
            {
                "id": "public_market_data",
                "title": "Public Market Data",
                "status": "active",
                "detail": self.public_market_data.health()["notes"],
            },
            {
                "id": "analysis",
                "title": "Market / News Analysis",
                "status": "active",
                "detail": self.analysis.health()["notes"],
            },
            {
                "id": "ai",
                "title": "Grok AI",
                "status": "paper-analysis" if self.settings.grok_paper_analysis else "fixture",
                "detail": (
                    self.grok.health()["notes"]
                    if self.settings.grok_paper_analysis
                    else self.ai.health()["notes"]
                ),
            },
            {
                "id": "decision",
                "title": "BUY / SELL / HOLD",
                "status": "active",
                "detail": "Fixture returns HOLD only. Proposals, not orders.",
            },
            {
                "id": "paper_account",
                "title": "Paper Account",
                "status": "active",
                "detail": self.paper_account.health()["notes"],
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
                "status": "paper",
                "detail": (
                    "Alpaca PAPER when configured. Simulated engine otherwise. "
                    "Live trading disabled."
                ),
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
            "banner": "PAPER SIMULATION — NO REAL TRADING",
            "orders_enabled": False,
            "broker_used": False,
            "trading_mode": self.settings.trading_mode,
            "broker": self._select_broker_name(),
            "kill_switch": self.kill_switch.snapshot(),
            "safety": safety,
            "config": self.settings.public_view(),
            "modules": {
                "market_data": self.market_data.health(),
                "public_market_data": self.public_market_data.health(),
                "analysis": self.analysis.health(),
                "ai": self.ai.health(),
                "grok": self.grok.health(),
                "paper_account": self.paper_account.health(),
                "risk": self.risk.health(),
                "broker_simulated": self.simulated_broker.health(),
                "broker_alpaca": self.alpaca_broker.health(),
                "database": self.repository.health(),
            },
            "architecture": self.architecture(),
            "market": self.repository.latest_series(),
            "analysis": self.repository.latest_analysis(),
            "decision": self.repository.latest_decision(),
            "account": self.paper_account.snapshot().to_dict(),
            "last_grok_cycle": self.last_grok_cycle,
            "last_benchmark": self.last_benchmark,
            "paper_session": self.paper_session.status(),
            "paper": {
                "orders": self.repository.list_paper_orders(20),
                "fills": self.repository.list_paper_fills(20),
                "positions": self.repository.list_paper_positions(),
                "performance": self.repository.latest_performance(),
            },
        }

    def dry_run(self, symbols: Optional[list[str]] = None) -> dict[str, Any]:
        """Walk the pipeline. Persist bars, analysis, HOLD, account. Place zero orders."""
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

        symbols = symbols or list(DEFAULT_SYMBOLS)
        snapshot = validate_snapshot(
            self.market_data.snapshot(symbols, limit=DRY_RUN_BARS)
        )
        for series in snapshot.series:
            self.repository.save_series(series)

        bundle = self.analysis.analyse(snapshot)
        for item in bundle.analyses:
            self.repository.save_analysis(item)

        account_before = self.paper_account.snapshot()
        self._persist_account(account_before)
        account_payload = account_before.to_dict()

        decisions: list[dict[str, Any]] = []
        last_verdict_reason = "HOLD does not produce an order."
        for item in bundle.analyses:
            proposed = self.ai.propose(snapshot, item)
            decision = proposed.decision
            if decision.action != Action.HOLD:
                raise OrderPlacementDisabledError(
                    "Fixture adapter must only emit HOLD."
                )
            decision_id = self.repository.record_decision(
                symbol=decision.symbol,
                action=decision.action.value,
                rationale=decision.rationale,
                model=decision.model,
                status="hold_fixture",
                confidence=decision.confidence,
                raw_response=decision.raw_response,
                market_snapshot_json=decision.market_snapshot_json,
                prompt_hash=decision.analysis_ref,
            )
            verdict = self.risk.review(
                decision,
                account=account_payload,
                positions=list(account_before.positions),
            )
            last_verdict_reason = verdict.reason
            if verdict.approved:
                raise OrderPlacementDisabledError(
                    "Risk engine must not approve orders in this build."
                )
            payload = decision.to_dict()
            payload["id"] = decision_id
            payload["risk_approved"] = False
            payload["risk_reason"] = verdict.reason
            decisions.append(payload)

        # Broker is intentionally not called. No fills.

        account_after = self.paper_account.snapshot()
        self._persist_account(account_after)

        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="dry_run",
            message="Dry run completed. Paper account unchanged. No orders placed.",
            details={
                "symbols": symbols,
                "timeframe": snapshot.timeframe,
                "source": snapshot.source,
                "bars": [bar.to_dict() for bar in snapshot.bars],
                "analysis": [item.to_dict() for item in bundle.analyses],
                "decisions": decisions,
                "account": account_after.to_dict(),
                "risk_approved": False,
                "risk_reason": last_verdict_reason,
                "broker_submit_calls": self.simulated_broker.submit_calls,
                "fill_count": account_after.fill_count,
                "orders_enabled": False,
                "orders_placed": 0,
            },
        )
        log.info("Dry run complete for %s — no orders placed", symbols)
        before_balances = {k: v for k, v in account_before.to_dict().items() if k != "as_of"}
        after_balances = {k: v for k, v in account_after.to_dict().items() if k != "as_of"}
        return {
            "ok": True,
            "orders_placed": 0,
            "fills": 0,
            "symbols": symbols,
            "timeframe": snapshot.timeframe,
            "analysis": [item.to_dict() for item in bundle.analyses],
            "decisions": decisions,
            "account": account_after.to_dict(),
            "account_unchanged": before_balances == after_balances,
            "risk_approved": False,
            "risk_reason": last_verdict_reason,
            "broker_submit_calls": self.simulated_broker.submit_calls,
            "market": [series.to_dict() for series in snapshot.series],
        }

    def paper_simulate(
        self,
        *,
        symbol: str = "SIM-UP",
        demo: bool = True,
        limit: int = 60,
    ) -> dict[str, Any]:
        """Run sequential paper simulation. Never calls a broker."""
        assert_safe_to_run(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        try:
            self.kill_switch.assert_clear()
        except KillSwitchEngagedError as exc:
            return {"ok": False, "blocked_by": "kill_switch", "message": str(exc)}

        series = generate_series(symbol, limit=limit, seed=42)
        source = DEMO_SIM_UP if demo else FixtureHoldSource()
        sim = PaperSimulator(risk=RiskEngine(allow_orders=False))
        report = sim.run(series, source=source, kill_switch=False)
        if report.get("look_ahead"):
            raise RuntimeError("Look-ahead bias detected.")
        self.repository.persist_paper_run(report)
        account = report["account"]
        self.repository.record_account_snapshot(
            mode="simulate",
            source="paper-sim",
            equity=account["account_equity"],
            cash=account["cash"],
            buying_power=account["buying_power"],
            portfolio_value=account["account_equity"],
            raw=account,
        )
        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="paper_sim",
            message="Paper simulation completed. No broker called.",
            details={
                "symbol": symbol,
                "fills": len(report["fills"]),
                "orders": len(report["orders"]),
                "broker_submit_calls": 0,
                "performance": report["performance"],
            },
        )
        report["broker_submit_calls"] = self.simulated_broker.submit_calls
        return report

    BANNER = "PAPER SIMULATION — NO REAL TRADING"

    def grok_paper_cycle(
        self,
        *,
        symbol: str = "SIM-UP",
        limit: int = 60,
        signal_index: int = 40,
    ) -> dict[str, Any]:
        """One Grok (or fixture) decision into the paper simulator. Never a broker."""
        assert_safe_to_run(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        try:
            self.kill_switch.assert_clear()
        except KillSwitchEngagedError as exc:
            return {
                "ok": False,
                "blocked_by": "kill_switch",
                "message": str(exc),
                "banner": self.BANNER,
                "broker": "NOT USED",
                "broker_submit_calls": 0,
                "risk": {"approved": False, "reason": "Kill switch engaged."},
                "paper_execution": "rejected",
            }

        series = generate_series(symbol, limit=limit, seed=42)
        if signal_index < 0 or signal_index >= len(series.candles) - 1:
            signal_index = max(0, len(series.candles) - 2)
        visible = replace(series, candles=series.candles[: signal_index + 1])
        analysis = analyse_series(visible)
        snapshot = MarketSnapshot(
            as_of=analysis.as_of,
            bars=tuple(),
            source="simulated",
            timeframe=visible.timeframe,
            series=(visible,),
        )
        account = self.paper_account.snapshot().to_dict()
        analyst = self.grok if self.settings.grok_paper_analysis else self.ai
        proposed = analyst.propose(
            snapshot,
            analysis,
            account=account,
            positions=list(account.get("positions") or []),
        )
        decision = proposed.decision
        self.repository.record_decision(
            symbol=decision.symbol,
            action=decision.action.value,
            rationale=decision.rationale,
            model=decision.model,
            status="grok_paper" if analyst.name == "grok" else "hold_fixture",
            confidence=decision.confidence,
            raw_response=decision.raw_response,
            market_snapshot_json=decision.market_snapshot_json,
            prompt_hash=decision.analysis_ref,
        )
        paper_action = PaperAction.HOLD
        if decision.action == Action.BUY:
            paper_action = PaperAction.BUY
        elif decision.action == Action.SELL:
            paper_action = PaperAction.SELL
        source = FrozenActionSource(paper_action, index=signal_index, name=analyst.name)
        sim = PaperSimulator(risk=self.risk)
        report = sim.run(series, source=source, kill_switch=False)
        if report.get("look_ahead"):
            raise RuntimeError("Look-ahead bias detected.")
        if self.simulated_broker.submit_calls or self.alpaca_broker.health().get("connected"):
            raise OrderPlacementDisabledError("Broker was touched during a paper cycle.")
        self.repository.persist_paper_run(report)
        account_after = report["account"]
        self.repository.record_account_snapshot(
            mode="simulate",
            source="grok-paper",
            equity=account_after["account_equity"],
            cash=account_after["cash"],
            buying_power=account_after["buying_power"],
            portfolio_value=account_after["account_equity"],
            raw=account_after,
        )
        orders = report.get("orders") or []
        fills = report.get("fills") or []
        execution = "none"
        if any(o.get("status") == "FILLED" or o.get("status") == "CLOSED" for o in orders) or fills:
            execution = "filled"
        elif any(o.get("status") == "REJECTED" for o in orders):
            execution = "rejected"
        elif any(o.get("status") == "CANCELLED" for o in orders):
            execution = "cancelled"
        elif paper_action == PaperAction.HOLD:
            execution = "none"
        risk_approved = execution == "filled"
        risk_reason = "HOLD does not produce an order."
        if orders:
            risk_reason = orders[-1].get("reason") or risk_reason
            if orders[-1].get("status") == "REJECTED":
                risk_approved = False
        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="grok_paper_cycle",
            message="Grok paper cycle completed. Broker not used.",
            details={
                "symbol": symbol,
                "action": decision.action.value,
                "model": decision.model,
                "execution": execution,
                "broker_submit_calls": 0,
            },
        )
        result = {
            "ok": True,
            "banner": self.BANNER,
            "live": False,
            "broker": "NOT USED",
            "broker_submit_calls": 0,
            "ai_model": "real Grok" if analyst.name == "grok" and self.settings.grok_paper_analysis else "fixture-hold",
            "ai_source": analyst.name,
            "ai_decision": decision.to_dict(),
            "confidence": decision.confidence,
            "reasoning": decision.rationale,
            "risk": {"approved": risk_approved, "reason": risk_reason},
            "paper_execution": execution,
            "look_ahead": False,
            "analysis": analysis.to_dict(),
            "paper": report,
        }
        self.last_grok_cycle = result
        return result

    def benchmark(self, *, grok_analyst: Any = None) -> dict[str, Any]:
        """Four-strategy paper benchmark. Never a broker. Never live."""
        assert_safe_to_run(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        from ai_trader.benchmark.runner import run_benchmark

        trapped: list[str] = []

        def trap(*args: Any, **kwargs: Any) -> None:
            trapped.append("submit")
            raise OrderPlacementDisabledError("Benchmark must not call Broker.submit.")

        self.broker.submit = trap  # type: ignore[method-assign]
        self.simulated_broker.submit = trap  # type: ignore[method-assign]
        self.alpaca_broker.submit = trap  # type: ignore[method-assign]
        analyst = grok_analyst
        if analyst is None:
            analyst = self.grok if self.settings.grok_paper_analysis else self.ai
        report = run_benchmark(grok_analyst=analyst, risk=self.risk)
        if trapped or self.simulated_broker.submit_calls:
            raise OrderPlacementDisabledError("Broker was touched during benchmark.")
        report["broker"] = "NOT USED"
        report["broker_submit_calls"] = 0
        report["banner"] = self.BANNER
        self.last_benchmark = report
        for run in report.get("runs") or []:
            for decision in run.get("ai_decisions") or []:
                self.repository.record_decision(
                    symbol=run.get("symbol"),
                    action=str(decision.get("action") or "HOLD"),
                    rationale=str(decision.get("reasoning") or ""),
                    model=str(decision.get("model") or report.get("grok_model") or "unknown"),
                    status="benchmark",
                    confidence=decision.get("confidence"),
                    market_snapshot_json=json.dumps(
                        {
                            "split": run.get("split"),
                            "bar": decision.get("bar"),
                            "broker": "NOT USED",
                            "live": False,
                        }
                    ),
                    prompt_hash=f"{run.get('split')}:{run.get('symbol')}:{decision.get('bar')}",
                )
        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="benchmark",
            message="Paper benchmark completed. Broker not used.",
            details={
                "run_count": report.get("run_count"),
                "grok_model": report.get("grok_model"),
                "headline": report.get("headline"),
                "broker_submit_calls": 0,
            },
        )
        return report

    def start_paper_session(
        self,
        *,
        symbol: str = "SIM-UP",
        bars: int = 24,
        timeframe: str = "5m",
        grok_frequency: int = 8,
        warmup: int = 8,
        stop_at: int | None = None,
        source: str = "simulated",
        continuous: bool = False,
        starting_balance: float | None = None,
    ) -> dict[str, Any]:
        """Sequential paper session. Repeated Grok decisions. Never a broker."""
        assert_safe_to_run(
            mode=self.settings.trading_mode,
            alpaca_base_url=self.settings.alpaca_base_url,
        )
        trapped: list[str] = []

        def trap(*args: Any, **kwargs: Any) -> None:
            trapped.append("submit")
            raise OrderPlacementDisabledError("Paper session must not call SimulatedBroker.submit.")

        # No session path may submit to any broker. Alpaca is observation only.
        self.simulated_broker.submit = trap  # type: ignore[method-assign]
        self.broker.submit = trap  # type: ignore[method-assign]
        self.alpaca_broker.submit = trap  # type: ignore[method-assign]
        analyst = self.grok if self.settings.grok_paper_analysis else self.ai
        config_kwargs: dict[str, Any] = {
            "symbol": symbol,
            "bars": bars,
            "timeframe": timeframe,
            "grok_frequency": grok_frequency,
            "warmup": warmup,
            "source": source,
            "continuous": continuous,
            "flatten_at_end": not continuous,
        }
        if starting_balance is not None:
            config_kwargs["starting_balance"] = float(starting_balance)
        config = PaperSessionConfig(**config_kwargs)
        feed = self.public_market_data if source == "public" else None
        report = self.paper_session.start(
            analyst=analyst,
            risk=self.risk,
            config=config,
            stop_at=stop_at,
            market_data=feed,
        )
        if trapped or self.simulated_broker.submit_calls:
            raise OrderPlacementDisabledError("Simulated broker was touched during a paper session.")
        report = self._attach_alpaca_paper(report)
        self.last_session = report
        if not continuous:
            self._persist_session(report)
        return report

    def _persist_session(self, report: dict[str, Any]) -> None:
        self.repository.persist_paper_run(
            {
                "symbol": report.get("symbol"),
                "signal_source": "grok-session",
                "orders": report.get("orders") or [],
                "fills": report.get("fills") or [],
                "positions": (
                    [report["position"]]
                    if isinstance(report.get("position"), dict)
                    else []
                ),
                "closed_positions": report.get("closed") or [],
                "performance": report.get("performance") or {},
            }
        )
        for decision in report.get("ai_decisions") or []:
            self.repository.record_decision(
                symbol=report.get("symbol"),
                action=str(decision.get("action") or "HOLD"),
                rationale=str(decision.get("reasoning") or ""),
                model=str(decision.get("model") or report.get("grok_model") or "unknown"),
                status="paper_session",
                confidence=decision.get("confidence"),
                market_snapshot_json=json.dumps(
                    {
                        "bar": decision.get("bar"),
                        "broker": report.get("broker") or "NOT USED",
                        "live": False,
                    }
                ),
                prompt_hash=f"session:{report.get('symbol')}:{decision.get('bar')}",
            )
        self.repository.record_event(
            level="INFO",
            source="pipeline",
            event_type="paper_session",
            message="Paper session completed. Live trading disabled.",
            details={
                "symbol": report.get("symbol"),
                "decisions": report.get("decisions"),
                "trades": report.get("trades"),
                "balance": report.get("balance"),
                "execution": report.get("execution"),
                "live": False,
                "alpaca_submit_calls": report.get("alpaca_submit_calls", 0),
            },
        )

    def _alpaca_paper_ready(self) -> bool:
        return (
            LIVE_TRADING_ALLOWED is False
            and self.settings.trading_mode == "paper"
            and self.settings.alpaca_configured()
        )

    def _attach_alpaca_paper(self, report: dict[str, Any]) -> dict[str, Any]:
        """Attach a **read-only** Alpaca paper account mirror, if configured.

        This used to submit an order. It no longer does, for three reasons:

        1. It ran its own weakened risk check — ``trades_today=0``,
           ``halted=False``, ``day_start_equity`` set to current equity — so
           the daily-loss limit and the trade cap could never fire on that path.
        2. Any fill it produced never reached ``PaperLedger``, so the internal
           book and the reported balance silently disagreed.
        3. It was the only code in the repository that sent an order to an
           external venue, reached directly from the Start button.

        The authoritative fill path is the internal simulator. Alpaca is now
        observation only, and ``submit`` is trapped by the caller.
        """
        payload = dict(report)
        payload["live"] = False
        payload["live_trading_allowed"] = False
        payload["alpaca_submit_calls"] = 0
        payload["broker_submit_calls"] = 0
        payload["execution"] = "simulated"
        payload["broker"] = "NOT USED"
        if LIVE_TRADING_ALLOWED:
            payload["status"] = "BLOCKED"
            payload["data_error"] = "Live trading is disabled."
            return payload
        running = bool(payload.get("running") and not payload.get("stopped"))
        if running:
            payload["status"] = "RUNNING"
            return payload
        if not self._alpaca_paper_ready():
            payload["status"] = payload.get("status") or "SIMULATED"
            return payload

        account = self.alpaca_broker.account()
        if not account.get("available"):
            payload["status"] = "SIMULATED"
            payload["alpaca_account"] = account
            payload["alpaca_error"] = account.get("reason") or "Alpaca paper unavailable."
            payload["alpaca_failure"] = account.get("failure")
            return payload

        # Observation only. The account balance shown to the user stays the
        # internal GBP paper book; the Alpaca mirror sits beside it.
        payload["status"] = "SIMULATED"
        payload["alpaca_account"] = {
            k: v for k, v in account.items() if k not in {"id", "account_number"}
        }
        try:
            payload["alpaca_positions"] = self.alpaca_broker.positions()
        except AlpacaPaperUnavailableError as exc:
            payload["alpaca_error"] = str(exc)
            payload["alpaca_failure"] = exc.failure
        return payload


    def stop_paper_session(self) -> dict[str, Any]:
        was_continuous = bool(getattr(self.paper_session.config, "continuous", False))
        report = self.paper_session.stop()
        if was_continuous:
            self._persist_session(report)
        return report

    def place_order(self, *args: Any, **kwargs: Any) -> None:
        raise OrderPlacementDisabledError(
            "The orchestrator will not place orders in the foundation build."
        )
