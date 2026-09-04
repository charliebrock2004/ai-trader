"""Walk candles sequentially. Repeated Grok paper decisions. Never a broker."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Optional

from ai_trader.account.simulated import STARTING_CASH
from ai_trader.ai.base import Analyst
from ai_trader.ai.fixture import FixtureAnalyst
from ai_trader.clock import Clock, default_clock
from ai_trader.exceptions import (
    HistoricalDataNotConfiguredError,
    InvalidMarketDataError,
    MarketDataUnavailableError,
    OrderPlacementDisabledError,
    StaleMarketDataError,
)
from ai_trader.fx.provider import FxProvider, FxRateUnavailableError
from ai_trader.instruments import instrument_for
from ai_trader.market_data.generator import generate_series
from ai_trader.market_data.validation import parse_utc
from ai_trader.paper.execution import ASSUMPTIONS
from ai_trader.paper.simulator import PaperSimulator
from ai_trader.risk.engine import RiskEngine
from ai_trader.safety import LIVE_TRADING_ALLOWED
from ai_trader.session.config import BANNER, PaperSessionConfig
from ai_trader.session.continuity import (
    INDICATOR_HISTORY_BARS,
    baseline_timestamp,
    fetch_limit,
    indicator_snapshot,
    resolve_trade_from_index,
)
from ai_trader.session.source import DeterministicFirstSource, RepeatingGrokSource
from ai_trader.types import CandleSeries


class PaperSession:
    """Start/stop paper session. STOP blocks new paper trades immediately."""

    def __init__(
        self,
        config: Optional[PaperSessionConfig] = None,
        *,
        analyst: Optional[Analyst] = None,
        risk: Optional[RiskEngine] = None,
        market_data: Any = None,
        poll_seconds: float = 15.0,
        clock: Optional[Clock] = None,
        fx: Optional[FxProvider] = None,
        policy: Any = None,
        on_persist: Optional[Any] = None,
        on_decision: Optional[Any] = None,
        budget: Any = None,
        gate_with_deterministic: bool = False,
    ) -> None:
        self.config = (config or PaperSessionConfig()).validate()
        self.analyst = analyst or FixtureAnalyst()
        self.risk = risk or RiskEngine(allow_orders=False)
        self.market_data = market_data
        self.clock = clock or default_clock()
        self.fx = fx
        self.policy = policy
        #: Called with the public report whenever session state advances, so a
        #: continuous run is durable rather than only persisted on Stop.
        self.on_persist = on_persist
        self.on_decision = on_decision
        self.on_candle_processed = None
        self.budget = budget
        self.gate_with_deterministic = bool(gate_with_deterministic)
        self.fx_rate = 1.0
        self.fx_detail: Optional[dict[str, Any]] = None
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.stopped = True
        self.running = False
        self.sim: Optional[PaperSimulator] = None
        self.source: Optional[Any] = None
        self.report: Optional[dict[str, Any]] = None
        self.last_processed_candle_ts: Optional[str] = self.config.last_processed_candle_ts
        self._stop_at: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._cursor = 0
        self._series: Optional[CandleSeries] = None
        self._generation = 0

    def stop(self) -> dict[str, Any]:
        self.stopped = True
        self.running = False
        self._stop_event.set()
        return self.status()

    def start(
        self,
        *,
        series: Optional[CandleSeries] = None,
        stop_at: Optional[int] = None,
        analyst: Optional[Analyst] = None,
        risk: Optional[RiskEngine] = None,
        config: Optional[PaperSessionConfig] = None,
        market_data: Any = None,
    ) -> dict[str, Any]:
        if LIVE_TRADING_ALLOWED:
            raise OrderPlacementDisabledError("Paper session refused: live trading flag must stay False.")
        if config is not None:
            self.config = config.validate()
        if analyst is not None:
            self.analyst = analyst
        if risk is not None:
            self.risk = risk
        if market_data is not None:
            self.market_data = market_data
        if self.risk.allow_orders:
            raise OrderPlacementDisabledError("Paper session refused: allow_orders must stay False.")
        if self.config.continuous:
            self.config = replace(self.config, flatten_at_end=False)
        if self._thread and self._thread.is_alive():
            self.stop()
            self._thread.join(timeout=1)
        self._generation += 1
        generation = self._generation
        self._stop_at = stop_at
        self._stop_event.clear()
        self.stopped = False
        self.running = True
        self.report = None
        self._cursor = 0
        self._series = None
        self.last_processed_candle_ts = self.config.last_processed_candle_ts
        if self.config.continuous and series is None and stop_at is None:
            self._thread = threading.Thread(
                target=self._run_loop, args=(None, generation), daemon=True
            )
            self._thread.start()
            return self.status()
        return self._run_blocking(series)

    def _run_blocking(self, series: Optional[CandleSeries]) -> dict[str, Any]:
        try:
            report = self._walk(series, finalize=not self.config.continuous)
            if not self.config.continuous:
                self.running = False
                self.stopped = True
                report = dict(report)
                report["grok"] = "STOPPED"
                report["running"] = False
                report["stopped"] = True
                if report.get("status") == "RUNNING":
                    report["status"] = "SIMULATED"
                self.report = report
            return report
        except Exception:
            if not self.config.continuous:
                self.running = False
                self.stopped = True
            raise

    def _run_loop(self, series: Optional[CandleSeries], generation: int) -> None:
        if generation != self._generation:
            return
        try:
            self._walk(series, finalize=False, generation=generation)
            while not self.stopped and generation == self._generation:
                if self._stop_event.wait(timeout=self.poll_seconds):
                    break
                if self.stopped or generation != self._generation:
                    break
                try:
                    fresh = self._load_series()
                except (
                    MarketDataUnavailableError,
                    InvalidMarketDataError,
                    HistoricalDataNotConfiguredError,
                    StaleMarketDataError,
                ) as exc:
                    with self._lock:
                        self.report = self._unavailable(exc)
                    break
                current = self._series
                if current is None or not current.candles:
                    continue
                last_ts = parse_utc(current.candles[-1].timestamp)
                extra = [c for c in fresh.candles if parse_utc(c.timestamp) > last_ts]
                if self.last_processed_candle_ts:
                    cutoff = parse_utc(self.last_processed_candle_ts)
                    extra = [c for c in extra if parse_utc(c.timestamp) > cutoff]
                if not extra:
                    continue
                combined = replace(current, candles=current.candles + tuple(extra))
                if self.sim is None or self.source is None:
                    break
                report = self.sim.extend(
                    combined,
                    start_index=self._cursor,
                    source=self.source,
                    kill_switch=False,
                    stop_check=lambda index, gen=generation: self._should_stop(index) or gen != self._generation,
                    on_processed=self._on_bar_processed,
                    finalize=False,
                )
                self._cursor = len(combined.candles)
                self._series = combined
                with self._lock:
                    self.report = self._public(report, combined)
                # Persist on every new bar, not only on Stop, so a worker
                # restart cannot silently discard a session's history.
                self._persist(self.report)
        except (
            MarketDataUnavailableError,
            InvalidMarketDataError,
            HistoricalDataNotConfiguredError,
            StaleMarketDataError,
        ) as exc:
            with self._lock:
                self.report = self._unavailable(exc)
        except Exception as exc:  # noqa: BLE001 — fail closed
            with self._lock:
                self.report = self._unavailable(exc)
        finally:
            if generation == self._generation:
                self.running = False
                self.stopped = True

    def _walk(
        self,
        series: Optional[CandleSeries],
        *,
        finalize: bool,
        generation: Optional[int] = None,
    ) -> dict[str, Any]:
        if generation is not None and generation != self._generation:
            return self.status()
        try:
            used = series or self._load_series()
        except (MarketDataUnavailableError, InvalidMarketDataError, HistoricalDataNotConfiguredError, StaleMarketDataError) as exc:
            if generation is None or generation == self._generation:
                self.running = False
                self.stopped = True
                self.report = self._unavailable(exc)
                return self.report
            return self.status()
        if generation is not None and generation != self._generation:
            return self.status()
        instrument = instrument_for(self.config.symbol)
        try:
            fx_rate = self._resolve_fx(instrument.quote_currency)
        except FxRateUnavailableError as exc:
            if generation is None or generation == self._generation:
                self.running = False
                self.stopped = True
                self.report = self._unavailable(exc)
                return self.report
            return self.status()
        self.sim = PaperSimulator(
            starting_cash=self.config.starting_balance,
            risk=self.risk,
            flatten_at_end=self.config.flatten_at_end,
            instrument=instrument,
            fx_rate=fx_rate,
            base_currency=self.config.base_currency,
            policy=self.policy,
            on_decision=self.on_decision,
        )
        # Bars already on the tape at Start are history unless a persisted
        # last_processed_candle_ts says some of them arrived after the last
        # live bar this worker actually handled. Warm-up never opens a position.
        if self.config.continuous and not self.config.trade_historical_bars:
            self.sim.trade_from_index = resolve_trade_from_index(
                used.candles, self.last_processed_candle_ts
            )
        if self.gate_with_deterministic:
            self.source = DeterministicFirstSource(
                self.analyst,
                budget=self.budget,
                warmup=self.config.warmup,
                account_fn=lambda: self.sim.ledger.snapshot().to_dict() if self.sim else {},
                stop_fn=lambda: self.stopped or (
                    generation is not None and generation != self._generation
                ),
            )
        else:
            self.source = RepeatingGrokSource(
                self.analyst,
                frequency=self.config.grok_frequency,
                warmup=self.config.warmup,
                account_fn=lambda: self.sim.ledger.snapshot().to_dict() if self.sim else {},
                stop_fn=lambda: self.stopped or (
                    generation is not None and generation != self._generation
                ),
            )
        report = self.sim.run(
            used,
            source=self.source,
            kill_switch=False,
            stop_check=lambda index: self._should_stop(index)
            or (generation is not None and generation != self._generation),
            on_processed=self._on_bar_processed,
            finalize=finalize,
        )
        if report.get("look_ahead"):
            raise RuntimeError("Look-ahead bias detected in paper session.")
        self._series = used
        self._cursor = len(used.candles)
        if (
            used.candles
            and self.sim.trade_from_index >= len(used.candles)
        ):
            # First session / nothing new: baseline so a restart does not
            # retroactively trade this warm-up window.
            stamp = baseline_timestamp(used.candles, self.sim.trade_from_index)
            if stamp:
                self._mark_processed(stamp)
        self.report = self._public(report, used)
        self._persist(self.report)
        return self.report

    def _resolve_fx(self, quote_currency: str) -> float:
        """Base units per quote unit. Fails closed if a rate is needed and absent."""
        base = self.config.base_currency
        if quote_currency == base:
            self.fx_rate = 1.0
            self.fx_detail = {"base": base, "quote": base, "rate": 1.0, "source": "same-currency"}
            return 1.0
        if self.fx is None:
            raise FxRateUnavailableError(
                f"{self.config.symbol} is quoted in {quote_currency} but no FX provider "
                f"is configured. Refusing to treat {quote_currency} as {base}.",
                failure="not_configured",
            )
        rate = self.fx.rate(quote_currency, base)
        self.fx_rate = float(rate.rate)
        self.fx_detail = rate.to_dict()
        return self.fx_rate

    def _mark_processed(self, timestamp: str) -> None:
        if not timestamp:
            return
        self.last_processed_candle_ts = timestamp
        callback = self.on_candle_processed
        if callback is None:
            return
        try:
            callback(timestamp)
        except Exception:  # noqa: BLE001 — persistence must never kill the session
            pass

    def _on_bar_processed(self, index: int, visible: CandleSeries) -> None:
        if not visible.candles:
            return
        candle = visible.candles[min(index, len(visible.candles) - 1)]
        self._mark_processed(candle.timestamp)

    def _persist(self, report: dict[str, Any]) -> None:
        if self.on_persist is None:
            return
        try:
            self.on_persist(report)
        except Exception:  # noqa: BLE001 — persistence must never kill the session
            pass

    def _load_series(self) -> CandleSeries:
        if self.config.source == "simulated":
            return generate_series(
                self.config.symbol,
                timeframe=self.config.timeframe,
                limit=fetch_limit(
                    bars=self.config.bars,
                    continuous=self.config.continuous,
                    source=self.config.source,
                ),
                seed=self.config.seed,
                source="simulated",
            )
        if self.config.source != "public":
            raise HistoricalDataNotConfiguredError(
                f"Market-data source '{self.config.source}' is not wired."
            )
        provider = self.market_data
        if provider is None:
            from ai_trader.market_data.public import PublicCryptoFeed

            provider = PublicCryptoFeed()
            self.market_data = provider
        return provider.candles(
            self.config.symbol,
            timeframe=self.config.timeframe,
            limit=fetch_limit(
                bars=self.config.bars,
                continuous=self.config.continuous,
                source=self.config.source,
            ),
        )

    def _should_stop(self, index: int) -> bool:
        if self.stopped:
            return True
        if self._stop_at is not None and index >= self._stop_at:
            self.stopped = True
            return True
        return False

    def _grok_label(self) -> str:
        if self.running and not self.stopped:
            return "RUNNING"
        return "STOPPED"

    def _model_label(self) -> str:
        analyst = self.analyst
        grok_ready = (
            getattr(analyst, "name", "") == "grok"
            and bool(getattr(analyst, "paper_requested", False))
            and bool(getattr(analyst, "is_configured", lambda: False)())
        )
        return "real Grok" if grok_ready else "fixture-hold"

    def _is_public(self) -> bool:
        return self.config.source == "public"

    def _flags(self) -> dict[str, Any]:
        public = self._is_public()
        instrument = instrument_for(self.config.symbol)
        return {
            "ok": True,
            "banner": BANNER,
            "live": False,
            "broker": "NOT USED",
            "broker_submit_calls": 0,
            "live_trading_allowed": False,
            "real_market_data": public,
            "market_data": "public" if public else "simulated",
            "look_ahead": False,
            "execution": "simulated",
            "currency": self.config.base_currency,
            "quote_currency": instrument.quote_currency,
            "fx_rate": self.fx_rate,
            "fx": self.fx_detail,
        }

    def _continuity_fields(self, series: Optional[CandleSeries] = None) -> dict[str, Any]:
        tape = series or self._series
        candles = tape.candles if tape is not None else ()
        snap = indicator_snapshot(candles)
        trade_from = self.sim.trade_from_index if self.sim is not None else None
        return {
            "last_processed_candle_ts": self.last_processed_candle_ts,
            "latest_candle_ts": snap.get("latest_candle_ts"),
            "sma10": snap.get("sma10"),
            "sma20": snap.get("sma20"),
            "sma_relationship": snap.get("sma_relationship"),
            "indicator_history_bars": snap.get("indicator_history_bars") or 0,
            "indicator_history_required": INDICATOR_HISTORY_BARS,
            "trade_from_index": trade_from,
        }

    def _unavailable(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, InvalidMarketDataError):
            failure = getattr(exc, "failure", None) or "malformed"
        else:
            failure = getattr(exc, "failure", None) or "unavailable"
        message = str(exc)
        payload = {
            **self._flags(),
            "ok": False,
            # _flags() reports the *configured* feed. Nothing arrived, so the
            # claim is withdrawn: a dashboard reading this field alone must not
            # be able to conclude the desk has real prices when it has none.
            "real_market_data": False,
            "market_data": "unavailable",
            "grok": "STOPPED",
            "grok_model": self._model_label(),
            "running": False,
            "stopped": True,
            "stopped_at": 0,
            "status": "STOPPED",
            "balance": self.config.starting_balance,
            "today_pnl": 0.0,
            "decision": "HOLD",
            "current_decision": "HOLD",
            "position": "flat",
            "open_pnl": 0.0,
            "trades": 0,
            "maximum_drawdown": 0.0,
            "last_decision_at": None,
            "decisions": 0,
            "ai_decisions": [],
            "failures": [{"action": "HOLD", "failure": failure, "reasoning": message}],
            "timeouts": [],
            "data_error": message,
            "data_failure": failure,
            "config": self.config.public(),
            "assumptions": ASSUMPTIONS,
            "symbol": self.config.symbol,
            "bars": 0,
            "account": {"starting_cash": self.config.starting_balance, "account_equity": self.config.starting_balance},
            "orders": [],
            "fills": [],
            "closed": [],
            "performance": {"total_trades": 0, "maximum_drawdown": 0.0},
            "last_price": None,
            **self._continuity_fields(),
        }
        return payload

    def _public(self, report: dict[str, Any], series: CandleSeries) -> dict[str, Any]:
        account = report.get("account") or {}
        positions = report.get("positions") or []
        open_pos = positions[0] if positions else None
        decisions = list(self.source.decisions if self.source else [])
        last = decisions[-1] if decisions else None
        failures = [d for d in decisions if d.get("failure")]
        timeouts = [d for d in failures if d.get("failure") == "timeout"]
        performance = report.get("performance") or {}
        running = self.running and not self.stopped
        return {
            **self._flags(),
            "grok": self._grok_label(),
            "grok_model": self._model_label(),
            "running": running,
            "stopped": self.stopped,
            "stopped_at": report.get("stopped_at"),
            "status": "RUNNING" if running else "SIMULATED",
            "balance": account.get("account_equity", self.config.starting_balance),
            "today_pnl": account.get("daily_pnl", 0.0),
            "decision": (last or {}).get("action", "HOLD"),
            "current_decision": (last or {}).get("action", "HOLD"),
            "position": open_pos or "flat",
            "open_pnl": account.get("unrealised_pnl", 0.0),
            "trades": performance.get("total_trades", 0),
            "maximum_drawdown": performance.get("maximum_drawdown", 0.0),
            "last_decision_at": (last or {}).get("timestamp"),
            "decisions": len(decisions),
            "ai_decisions": decisions,
            "failures": failures,
            "timeouts": timeouts,
            "data_error": None,
            "data_failure": None,
            "config": self.config.public(),
            "assumptions": ASSUMPTIONS,
            "symbol": series.symbol,
            "bars": len(series.candles),
            "account": account,
            "orders": report.get("orders") or [],
            "fills": report.get("fills") or [],
            "closed": report.get("closed_positions") or [],
            "performance": performance,
            "last_price": series.candles[-1].close if series.candles else None,
            **self._continuity_fields(series),
        }

    def status(self) -> dict[str, Any]:
        if self.report and not self.running:
            payload = dict(self.report)
            payload["grok"] = "STOPPED"
            payload["running"] = False
            payload["stopped"] = True
            return payload
        balance = self.config.starting_balance
        today = 0.0
        open_pnl = 0.0
        position: Any = "flat"
        dd = 0.0
        trades = 0
        if self.sim is not None:
            snap = self.sim.ledger.snapshot()
            data = snap.to_dict()
            balance = data.get("account_equity", balance)
            today = data.get("daily_pnl", 0.0)
            open_pnl = data.get("unrealised_pnl", 0.0)
            dd = self.sim.max_drawdown
            opens = self.sim.ledger.open_positions()
            position = opens[0].to_dict() if opens else "flat"
            trades = len(self.sim.ledger.closed_positions)
        last = (self.source.decisions[-1] if self.source and self.source.decisions else None)
        running = self.running and not self.stopped
        last_price = None
        bars = 0
        if self._series and self._series.candles:
            last_price = self._series.candles[-1].close
            bars = len(self._series.candles)
        elif self.report:
            last_price = self.report.get("last_price")
            bars = int(self.report.get("bars") or 0)
        return {
            **self._flags(),
            "grok": self._grok_label(),
            "grok_model": self._model_label(),
            "running": running,
            "stopped": self.stopped,
            "status": "RUNNING" if running else ("STOPPED" if self.stopped else "SIMULATED"),
            "balance": balance,
            "today_pnl": today,
            "decision": (last or {}).get("action", "HOLD"),
            "current_decision": (last or {}).get("action", "HOLD"),
            "position": position,
            "open_pnl": open_pnl,
            "trades": trades,
            "maximum_drawdown": dd,
            "last_decision_at": (last or {}).get("timestamp"),
            "decisions": len(self.source.decisions) if self.source else 0,
            "ai_decisions": list(self.source.decisions) if self.source else [],
            "failures": [],
            "timeouts": [],
            "data_error": None,
            "data_failure": None,
            "config": self.config.public(),
            "starting_cash": STARTING_CASH,
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "last_price": last_price,
            "bars": bars,
            **self._continuity_fields(),
        }
