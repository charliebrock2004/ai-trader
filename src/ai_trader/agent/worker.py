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
        self._ensure_columns()

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

    def start(self, **payload: Any) -> dict[str, Any]:
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
        starting = self._persisted_equity()
        report = self.runtime.orchestrator.start_paper_session(
            symbol=str(payload.get("symbol") or "BTC-USD"),
            bars=int(payload.get("bars") or 24),
            timeframe=str(payload.get("timeframe") or "5m"),
            grok_frequency=int(payload.get("grok_frequency") or 8),
            warmup=int(payload.get("warmup") or 8),
            source=str(payload.get("source") or "public"),
            continuous=True,
            starting_balance=starting,
        )
        if report.get("balance") is not None:
            self._remember_equity(report.get("balance"))
        self._ensure_cycle_loop()
        return self.status(paper=report)

    def stop(self) -> dict[str, Any]:
        self._set_desired(False)
        self._stop.set()
        report = self.runtime.orchestrator.stop_paper_session()
        if report.get("balance") is not None:
            self._remember_equity(report.get("balance"))
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
        elif last.get("notes"):
            hold_reason = last.get("notes")
        elif last.get("final_action") == "HOLD":
            hold_reason = last.get("policy_reason") or last.get("risk_reason") or last.get("stage")
        if running and session_ready and not hold_reason:
            hold_reason = paper.get("data_error")
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
            },
            "currency": "GBP",
            "engine": "python-worker",
        }
        if paper.get("data_error") and not running:
            merged["data_error"] = paper.get("data_error")
        return merged
