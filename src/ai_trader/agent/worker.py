"""Durable paper desk worker.

The browser is the control panel. This process owns the session: Start
spawns the loop, Stop ends it, a worker restart recovers a session that
was asked to keep running. Live trading is impossible here.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.survival.latch import AgentTerminatedError


class DeskWorker:
    """One loop for spot paper trading plus event-driven cycles."""

    def __init__(self, runtime: Any, *, cycle_seconds: float = 30.0) -> None:
        self.runtime = runtime
        self.cycle_seconds = max(5.0, float(cycle_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Start and Stop are serialised against each other. Over stdio commands
        # arrived one at a time; over HTTP a double-clicked button or a retrying
        # tab can deliver two Starts at once, and interleaving them would tear
        # down a live session halfway through building its replacement.
        self._control_lock = threading.Lock()
        self._ensure_columns()
        self.runtime.orchestrator.paper_session.on_candle_processed = self._remember_processed

    def _store(self):
        return self.runtime.repository.records

    def _ensure_columns(self) -> None:
        conn = self.runtime.repository.conn
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_life)").fetchall()}
        if "desired_running" not in cols:
            conn.execute(
                "ALTER TABLE agent_life ADD COLUMN desired_running INTEGER NOT NULL DEFAULT 0"
            )
        if "paper_equity" not in cols:
            conn.execute("ALTER TABLE agent_life ADD COLUMN paper_equity REAL")
        if "last_processed_candle_ts" not in cols:
            conn.execute("ALTER TABLE agent_life ADD COLUMN last_processed_candle_ts TEXT")
        conn.commit()

    def _set_desired(self, running: bool) -> None:
        self._store().update_agent_life(desired_running=1 if running else 0)

    def desired_running(self) -> bool:
        life = self._store().agent_life() or {}
        return bool(life.get("desired_running"))

    def _persisted_equity(self) -> Optional[float]:
        life = self._store().agent_life() or {}
        value = life.get("paper_equity")
        try:
            equity = float(value)
        except (TypeError, ValueError):
            return None
        if equity <= 0:
            return None
        return equity

    def _remember_equity(self, equity: Any) -> None:
        try:
            value = float(equity)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        self._store().update_agent_life(paper_equity=value)

    def _last_processed_ts(self) -> Optional[str]:
        life = self._store().agent_life() or {}
        stamp = life.get("last_processed_candle_ts")
        if stamp is None:
            return None
        text = str(stamp).strip()
        return text or None

    def _remember_processed(self, timestamp: Any) -> None:
        stamp = str(timestamp or "").strip()
        if not stamp:
            return
        self._store().update_agent_life(last_processed_candle_ts=stamp)

    def _already_running(self) -> bool:
        session = self.runtime.orchestrator.paper_session
        return bool(session.running and not session.stopped)

    def start(self, **payload: Any) -> dict[str, Any]:
        with self._control_lock:
            return self._start_locked(**payload)

    def _start_locked(self, **payload: Any) -> dict[str, Any]:
        if LIVE_TRADING_ALLOWED:
            return {
                "ok": False,
                "running": False,
                "stopped": True,
                "live": False,
                "data_error": "Live trading is disabled.",
            }
        try:
            self.runtime.agent.assert_alive()
        except AgentTerminatedError as exc:
            return {
                "ok": False,
                "running": False,
                "stopped": True,
                "terminated": True,
                "live": False,
                "data_error": str(exc),
            }
        self._set_desired(True)
        # Start is idempotent. A second Start against a live session used to
        # replace it, which reset the bar cursor and discarded the open
        # position — the account survived, but the session's history did not.
        # Asking a running desk to run is not a request to restart it.
        if self._already_running():
            self._ensure_cycle_loop()
            return self.status()
        starting = self._persisted_equity()
        source = str(payload.get("source") or "public")
        bars = int(payload.get("bars") or 24)
        if source == "public":
            from ai_trader.session.continuity import CONTINUOUS_FETCH_BARS

            bars = max(bars, CONTINUOUS_FETCH_BARS)
        report = self.runtime.orchestrator.start_paper_session(
            symbol=str(payload.get("symbol") or "BTC-USD"),
            bars=bars,
            timeframe=str(payload.get("timeframe") or "5m"),
            grok_frequency=int(payload.get("grok_frequency") or 8),
            warmup=int(payload.get("warmup") or 8),
            source=source,
            continuous=True,
            starting_balance=starting,
            last_processed_candle_ts=self._last_processed_ts(),
        )
        if report.get("balance") is not None:
            self._remember_equity(report.get("balance"))
        self._checkpoint()
        self._ensure_cycle_loop()
        return self.status(paper=report)

    def stop(self) -> dict[str, Any]:
        with self._control_lock:
            self._set_desired(False)
            self._stop.set()
            report = self.runtime.orchestrator.stop_paper_session()
            if report.get("balance") is not None:
                self._remember_equity(report.get("balance"))
            self._checkpoint()
            return self.status(paper=report)

    def shutdown(self) -> None:
        """Halt loops for process exit. Does not clear the recover latch."""
        self._stop.set()
        try:
            self.runtime.orchestrator.paper_session.stop()
        except Exception:
            pass

    def recover(self) -> None:
        """Restart a session that was asked to keep running before this process died."""
        if not self.desired_running():
            return
        try:
            self.runtime.agent.assert_alive()
        except AgentTerminatedError:
            self._set_desired(False)
            return
        session = self.runtime.orchestrator.paper_session
        if session.running and not session.stopped:
            self._ensure_cycle_loop()
            return
        try:
            self.start(symbol="BTC-USD", source="public", timeframe="5m", continuous=True)
        except Exception:
            self._set_desired(False)

    def _ensure_cycle_loop(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._cycle_loop, daemon=True, name="ai-trader-desk"
            )
            self._thread.start()

    def _cycle_loop(self) -> None:
        # First event cycle is delayed so Start cannot hang on BLS / Grok.
        delay = 2.0
        while not self._stop.wait(timeout=delay):
            delay = self.cycle_seconds
            if not self.desired_running():
                break
            try:
                self.runtime.agent.assert_alive()
            except AgentTerminatedError:
                self._set_desired(False)
                self.runtime.orchestrator.stop_paper_session()
                break
            try:
                self.runtime.agent.run_cycle()
            except Exception:
                pass
            paper = self.runtime.orchestrator.paper_session
            if paper.sim is not None:
                try:
                    equity = paper.sim.ledger.equity()
                    self._remember_equity(equity)
                    self.runtime.agent.survival.observe(equity, reason="paper session mark")
                except Exception:
                    pass
            self._checkpoint()

    def _checkpoint(self) -> None:
        try:
            from ai_trader.persist import checkpoint

            checkpoint(self.runtime.settings.resolve_database_path())
        except Exception:
            pass

    def _persistence_view(self) -> dict[str, Any]:
        try:
            from ai_trader.persist import persistence_status

            return persistence_status()
        except Exception:
            return {
                "kind": "ephemeral",
                "durable": False,
                "warning": "Persistence status unavailable.",
            }

    def _grok_usage(self) -> dict[str, Any]:
        settings = self.runtime.settings
        connected = bool(settings.grok_configured())
        budget = getattr(self.runtime, "budget", None)
        snap = budget.snapshot() if budget is not None else {}
        return {
            "connected": connected,
            "status": "connected" if connected else "disconnected",
            "model": settings.xai_model or "grok-4.3",
            "calls_today": int(snap.get("calls_today") or 0),
            "daily_budget": int(snap.get("daily_budget") or settings.grok_daily_call_budget),
            "remaining": int(snap.get("remaining") if snap.get("remaining") is not None else settings.grok_daily_call_budget),
            "estimated_cost": float(snap.get("estimated_cost") or 0.0),
            "min_interval_seconds": int(
                snap.get("min_interval_seconds") or settings.grok_min_interval_seconds
            ),
            "last_call_at": snap.get("last_call_at"),
            "allowed": bool(snap.get("allowed")) if snap else False,
            "filter": "SMA 10/20 cross",
            "operates_without_grok": True,
        }

    def status(self, paper: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        paper = paper or self.runtime.orchestrator.paper_session.status()
        agent = self.runtime.agent.status()
        thread = self.runtime.orchestrator.paper_session._thread
        worker_alive = bool(thread and thread.is_alive()) or bool(
            paper.get("running") and not paper.get("stopped")
        )
        cycle_alive = bool(self._thread and self._thread.is_alive())
        session_ready = paper.get("last_price") is not None or int(paper.get("bars") or 0) > 0
        running = bool(paper.get("running") and not paper.get("stopped"))
        last = agent.get("last_decision") or {}
        persisted = self._persisted_equity()
        paper_balance = paper.get("balance")
        if paper_balance is None:
            paper_balance = persisted if persisted is not None else agent.get("account", {}).get("equity", 100)
        hold_reason = None
        if not running:
            hold_reason = paper.get("data_error") or "Stopped. New paper trades blocked."
        elif not session_ready:
            hold_reason = "Starting. Loading market data."
        elif paper.get("data_error"):
            hold_reason = paper.get("data_error")
        elif last.get("kind") == "spot":
            hold_reason = last.get("notes") or last.get("policy_reason") or last.get("risk_reason")
        else:
            # The operational path is the BTC paper session. Event-pipeline HOLDs
            # (no venue book / unverified BLS) are recorded, but they must not
            # be presented as the reason the spot desk did not fill.
            symbol = paper.get("symbol") or "BTC-USD"
            timeframe = paper.get("timeframe") or "5m"
            hold_reason = (
                f"{symbol} {timeframe} paper session is running against public candles. "
                "No paper fill this bar."
            )
        account = dict(agent.get("account") or {})
        account["equity"] = paper_balance
        account["daily_pnl"] = paper.get("today_pnl", account.get("daily_pnl", 0))
        if paper.get("open_pnl") is not None:
            account["unrealised_pnl"] = paper.get("open_pnl")
        survival = dict(agent.get("survival") or {})
        if running or persisted is not None:
            survival["equity"] = paper_balance
        merged = {
            **agent,
            "ok": True,
            "live": False,
            "live_trading_allowed": False,
            "broker": "NOT USED",
            "broker_submit_calls": 0,
            "look_ahead": False,
            "real_market_data": paper.get("real_market_data", False),
            "banner": "PAPER SIMULATION — NO REAL TRADING",
            "running": running,
            "stopped": not running,
            "worker_alive": worker_alive or cycle_alive,
            "session_ready": bool(session_ready),
            "grok": "RUNNING" if running and session_ready else ("STARTING" if running else "STOPPED"),
            "status": "RUNNING" if running and session_ready else ("STARTING" if running else "STOPPED"),
            "balance": paper_balance,
            "today_pnl": paper.get("today_pnl", 0),
            "current_decision": paper.get("current_decision") or last.get("final_action") or "HOLD",
            "decision": paper.get("decision") or last.get("final_action") or "HOLD",
            "position": paper.get("position", "flat"),
            "open_pnl": paper.get("open_pnl", 0),
            "trades": paper.get("trades", 0),
            "symbol": paper.get("symbol") or "BTC-USD",
            "timeframe": paper.get("timeframe") or "5m",
            "last_price": paper.get("last_price"),
            "bars": paper.get("bars", 0),
            "data_error": paper.get("data_error"),
            "hold_reason": hold_reason,
            "account": account,
            "survival": survival,
            "paper": {
                "running": running,
                "symbol": paper.get("symbol"),
                "last_price": paper.get("last_price"),
                "bars": paper.get("bars"),
                "last_processed_candle_ts": paper.get("last_processed_candle_ts"),
                "latest_candle_ts": paper.get("latest_candle_ts"),
                "sma10": paper.get("sma10"),
                "sma20": paper.get("sma20"),
            },
            "last_processed_candle_ts": paper.get("last_processed_candle_ts") or self._last_processed_ts(),
            "latest_candle_ts": paper.get("latest_candle_ts"),
            "sma10": paper.get("sma10"),
            "sma20": paper.get("sma20"),
            "sma_relationship": paper.get("sma_relationship"),
            "indicator_history_bars": paper.get("indicator_history_bars"),
            "trade_from_index": paper.get("trade_from_index"),
            "currency": "GBP",
            "engine": "python-worker",
            "persistence": self._persistence_view(),
            "grok_usage": self._grok_usage(),
        }
        if paper.get("data_error") and not running:
            merged["data_error"] = paper.get("data_error")
        return merged
