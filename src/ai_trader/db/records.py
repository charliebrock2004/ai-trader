"""The permanent decision record.

Every decision the agent reaches is written here, including the ones where it
did nothing. A HOLD is a decision. A rejected opportunity is a decision. The
reason for each is stored, not inferred.

The point is that these six questions must be answerable from the database
alone, months later, without re-running anything:

    What did the agent know?              -> decision_inputs, official_data,
                                             market_snapshots
    What did it believe?                  -> decisions.model_probability
    What did the deterministic layer say? -> decisions.net_edge, fees, spread
    What did the analyst recommend?       -> decisions.ai_*
    Why was it allowed or refused?        -> decisions.policy_*, risk_*
    What happened next?                   -> outcomes, contract_positions

All writes go through one lock. The connection is shared across the session
thread and the request thread, and SQLite's ``check_same_thread=False`` makes
that possible but not safe on its own.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from ai_trader.clock import Clock, default_clock


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _row(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


class RecordStore:
    """Reads and writes the agent audit trail."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Optional[Clock] = None) -> None:
        self.conn = conn
        self.clock = clock or default_clock()
        self._lock = threading.RLock()

    def _now(self) -> str:
        return self.clock.now_iso()

    def _write(self, sql: str, params: tuple) -> int:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return int(cur.lastrowid)

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(self.conn.execute(sql, params).fetchall())

    def _one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        with self._lock:
            return _row(self.conn.execute(sql, params).fetchone())

    # -- markets ----------------------------------------------------------
    def upsert_market(
        self,
        *,
        venue: str,
        ticker: str,
        kind: str,
        question: Optional[str] = None,
        event_key: Optional[str] = None,
        resolution_source: Optional[str] = None,
        resolution_time: Optional[str] = None,
        settlement_rules: Optional[str] = None,
        tick_size: Optional[float] = None,
        min_order: Optional[float] = None,
        max_order: Optional[float] = None,
        fee_model: Optional[str] = None,
        quote_currency: str = "USD",
        raw: Any = None,
    ) -> int:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO markets (
                    created_at, venue, ticker, kind, question, event_key,
                    resolution_source, resolution_time, settlement_rules,
                    tick_size, min_order, max_order, fee_model, quote_currency, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue, ticker) DO UPDATE SET
                    question=excluded.question,
                    event_key=excluded.event_key,
                    resolution_source=excluded.resolution_source,
                    resolution_time=excluded.resolution_time,
                    settlement_rules=excluded.settlement_rules,
                    tick_size=excluded.tick_size,
                    min_order=excluded.min_order,
                    max_order=excluded.max_order,
                    fee_model=excluded.fee_model,
                    quote_currency=excluded.quote_currency,
                    raw_json=excluded.raw_json
                """,
                (
                    self._now(), venue, ticker, kind, question, event_key,
                    resolution_source, resolution_time, settlement_rules,
                    tick_size, min_order, max_order, fee_model, quote_currency, _json(raw),
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM markets WHERE venue=? AND ticker=?", (venue, ticker)
            ).fetchone()
            return int(row["id"])

    def market(self, ticker: str) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM markets WHERE ticker=? ORDER BY id DESC LIMIT 1", (ticker,))

    def record_market_snapshot(
        self,
        *,
        market_id: int,
        ticker: str,
        observed_at: str,
        yes_bid: Optional[float],
        yes_ask: Optional[float],
        mid: Optional[float],
        spread: Optional[float],
        top_depth: Optional[float],
        total_depth: Optional[float],
        book: Any,
        source: str,
    ) -> int:
        return self._write(
            """
            INSERT INTO market_snapshots (
                created_at, observed_at, market_id, ticker, yes_bid, yes_ask,
                mid, spread, top_depth, total_depth, book_json, source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(), observed_at, market_id, ticker, yes_bid, yes_ask,
                mid, spread, top_depth, total_depth, _json(book), source,
            ),
        )

    # -- official data ----------------------------------------------------
    def record_official_data(
        self,
        *,
        series_key: str,
        release_key: str,
        source: str,
        observed_at: str,
        status: str,
        scheduled_at: Optional[str] = None,
        published_at: Optional[str] = None,
        value: Optional[float] = None,
        unit: Optional[str] = None,
        verified: bool = False,
        verification_method: Optional[str] = None,
        second_read: Optional[float] = None,
        notes: Optional[str] = None,
        raw: Any = None,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO official_data (
                    created_at, series_key, release_key, source, scheduled_at,
                    published_at, observed_at, value, unit, status, verified,
                    verification_method, second_read, notes, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(series_key, release_key, observed_at) DO UPDATE SET
                    status=excluded.status,
                    value=excluded.value,
                    verified=excluded.verified,
                    verification_method=excluded.verification_method,
                    second_read=excluded.second_read,
                    notes=excluded.notes,
                    raw_json=excluded.raw_json
                """,
                (
                    self._now(), series_key, release_key, source, scheduled_at,
                    published_at, observed_at, value, unit, status, int(verified),
                    verification_method, second_read, notes, _json(raw),
                ),
            )
            self.conn.commit()
            if cur.lastrowid:
                return int(cur.lastrowid)
            row = self.conn.execute(
                "SELECT id FROM official_data WHERE series_key=? AND release_key=? AND observed_at=?",
                (series_key, release_key, observed_at),
            ).fetchone()
            return int(row["id"])

    def latest_official(self, series_key: str, release_key: str) -> Optional[dict[str, Any]]:
        return self._one(
            """
            SELECT * FROM official_data
            WHERE series_key=? AND release_key=?
            ORDER BY id DESC LIMIT 1
            """,
            (series_key, release_key),
        )

    def release_already_traded(self, release_key: str) -> bool:
        """Duplicate-event protection: has this release already produced a trade?"""
        row = self._one(
            """
            SELECT COUNT(*) AS n FROM decisions
            WHERE event_key = ? AND executed = 1
            """,
            (release_key,),
        )
        return bool(row and int(row["n"]) > 0)

    # -- opportunities ----------------------------------------------------
    def record_opportunity(self, payload: dict[str, Any]) -> int:
        return self._write(
            """
            INSERT INTO opportunities (
                created_at, cycle_id, market_id, ticker, event_key, official_data_id,
                side, model_probability, market_probability, fee_cost, spread_cost,
                gross_edge, net_edge, liquidity, time_to_resolution_seconds,
                data_confidence, resolution_confidence, rank_score, selected,
                reject_reason, inputs_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(),
                payload.get("cycle_id", ""),
                payload.get("market_id"),
                payload.get("ticker", ""),
                payload.get("event_key"),
                payload.get("official_data_id"),
                payload.get("side", "YES"),
                payload.get("model_probability"),
                payload.get("market_probability"),
                payload.get("fee_cost"),
                payload.get("spread_cost"),
                payload.get("gross_edge"),
                payload.get("net_edge"),
                payload.get("liquidity"),
                payload.get("time_to_resolution_seconds"),
                payload.get("data_confidence"),
                payload.get("resolution_confidence"),
                payload.get("rank_score"),
                int(bool(payload.get("selected"))),
                payload.get("reject_reason"),
                _json(payload.get("inputs")),
            ),
        )

    def list_opportunities(self, limit: int = 50, *, cycle_id: Optional[str] = None) -> list[dict[str, Any]]:
        if cycle_id:
            return self._query(
                "SELECT * FROM opportunities WHERE cycle_id=? ORDER BY id DESC LIMIT ?",
                (cycle_id, limit),
            )
        return self._query("SELECT * FROM opportunities ORDER BY id DESC LIMIT ?", (limit,))

    # -- decisions --------------------------------------------------------
    def record_decision(self, payload: dict[str, Any], inputs: Optional[list[dict[str, Any]]] = None) -> int:
        decision_id = self._write(
            """
            INSERT INTO decisions (
                created_at, cycle_id, kind, ticker, market_id, event_key, opportunity_id,
                model_probability, market_probability, gross_edge, net_edge, fees, spread,
                liquidity, ai_model, ai_action, ai_confidence, ai_bull, ai_bear,
                ai_invalidators, ai_raw, ai_validated, ai_failure,
                proposed_action, policy_action, policy_reason, survival_state, risk_multiplier,
                risk_approved, risk_reason, risk_json,
                final_action, executed, order_ref, stage, equity_before, cash_before,
                base_currency, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                payload.get("created_at") or self._now(),
                payload.get("cycle_id", ""),
                payload.get("kind", "spot"),
                payload.get("ticker"),
                payload.get("market_id"),
                payload.get("event_key"),
                payload.get("opportunity_id"),
                payload.get("model_probability"),
                payload.get("market_probability"),
                payload.get("gross_edge"),
                payload.get("net_edge"),
                payload.get("fees"),
                payload.get("spread"),
                payload.get("liquidity"),
                payload.get("ai_model"),
                payload.get("ai_action"),
                payload.get("ai_confidence"),
                payload.get("ai_bull"),
                payload.get("ai_bear"),
                _json(payload.get("ai_invalidators")),
                payload.get("ai_raw"),
                int(bool(payload.get("ai_validated"))),
                payload.get("ai_failure"),
                payload.get("proposed_action"),
                payload.get("policy_action"),
                payload.get("policy_reason"),
                payload.get("survival_state"),
                payload.get("risk_multiplier"),
                int(bool(payload.get("risk_approved"))),
                payload.get("risk_reason"),
                _json(payload.get("risk_json")),
                payload.get("final_action", "HOLD"),
                int(bool(payload.get("executed"))),
                payload.get("order_ref"),
                payload.get("stage"),
                payload.get("equity_before"),
                payload.get("cash_before"),
                payload.get("base_currency", "GBP"),
                payload.get("notes"),
            ),
        )
        for item in inputs or []:
            self.record_decision_input(decision_id, item)
        return decision_id

    def record_decision_input(self, decision_id: int, item: dict[str, Any]) -> int:
        return self._write(
            """
            INSERT INTO decision_inputs (decision_id, name, kind, value_json, source, as_of)
            VALUES (?,?,?,?,?,?)
            """,
            (
                decision_id,
                item.get("name", "input"),
                item.get("kind", "derived"),
                _json(item.get("value")) or "null",
                item.get("source"),
                item.get("as_of"),
            ),
        )

    def decision(self, decision_id: int) -> Optional[dict[str, Any]]:
        row = self._one("SELECT * FROM decisions WHERE id=?", (decision_id,))
        if row is None:
            return None
        row["inputs"] = self._query(
            "SELECT name, kind, value_json, source, as_of FROM decision_inputs WHERE decision_id=? ORDER BY id",
            (decision_id,),
        )
        row["outcome"] = self._one("SELECT * FROM outcomes WHERE decision_id=?", (decision_id,))
        row["orders"] = self._query(
            "SELECT * FROM contract_orders WHERE decision_id=? ORDER BY id", (decision_id,)
        )
        row["positions"] = self._query(
            "SELECT * FROM contract_positions WHERE decision_id=? ORDER BY id", (decision_id,)
        )
        return row

    def list_decisions(
        self,
        limit: int = 50,
        *,
        only_executed: bool = False,
        ticker: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if only_executed:
            clauses.append("executed = 1")
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self._query(
            f"SELECT * FROM decisions {where} ORDER BY id DESC LIMIT ?", tuple(params)
        )

    def decision_counts(self) -> dict[str, int]:
        rows = self._query(
            "SELECT final_action, COUNT(*) AS n FROM decisions GROUP BY final_action"
        )
        counts = {str(r["final_action"]): int(r["n"]) for r in rows}
        total = sum(counts.values())
        executed = self._one("SELECT COUNT(*) AS n FROM decisions WHERE executed=1")
        rejected = self._one(
            "SELECT COUNT(*) AS n FROM decisions WHERE executed=0 AND risk_approved=0"
        )
        counts["TOTAL"] = total
        counts["EXECUTED"] = int(executed["n"]) if executed else 0
        counts["NOT_EXECUTED"] = int(rejected["n"]) if rejected else 0
        return counts

    # -- outcomes ---------------------------------------------------------
    def record_outcome(
        self,
        *,
        decision_id: int,
        predicted_probability: float,
        resolved_outcome: int,
        resolved_at: str,
        ticker: Optional[str] = None,
        event_key: Optional[str] = None,
        market_probability: Optional[float] = None,
        realised_pnl_base: Optional[float] = None,
        predicted_edge: Optional[float] = None,
        realised_edge: Optional[float] = None,
        resolution_source: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        outcome = 1 if int(resolved_outcome) else 0
        brier = round((float(predicted_probability) - outcome) ** 2, 8)
        correct = 1 if (predicted_probability >= 0.5) == (outcome == 1) else 0
        return self._write(
            """
            INSERT INTO outcomes (
                created_at, decision_id, ticker, event_key, predicted_probability,
                market_probability, resolved_outcome, resolved_at, resolution_source,
                realised_pnl_base, predicted_edge, realised_edge, brier, correct, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(decision_id) DO UPDATE SET
                resolved_outcome=excluded.resolved_outcome,
                resolved_at=excluded.resolved_at,
                realised_pnl_base=excluded.realised_pnl_base,
                realised_edge=excluded.realised_edge,
                brier=excluded.brier,
                correct=excluded.correct,
                notes=excluded.notes
            """,
            (
                self._now(), decision_id, ticker, event_key, float(predicted_probability),
                market_probability, outcome, resolved_at, resolution_source,
                realised_pnl_base, predicted_edge, realised_edge, brier, correct, notes,
            ),
        )

    def list_outcomes(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM outcomes ORDER BY id DESC LIMIT ?", (limit,))

    # -- costs ------------------------------------------------------------
    def record_cost(
        self,
        *,
        category: str,
        description: str,
        amount_base: float,
        currency: str = "GBP",
        incurred_at: Optional[str] = None,
        units: Optional[float] = None,
        unit_name: Optional[str] = None,
        reference: Optional[str] = None,
    ) -> int:
        return self._write(
            """
            INSERT INTO costs (
                created_at, incurred_at, category, description, amount_base,
                currency, units, unit_name, reference
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(), incurred_at or self._now(), category, description,
                round(float(amount_base), 6), currency, units, unit_name, reference,
            ),
        )

    def total_costs(self) -> float:
        row = self._one("SELECT COALESCE(SUM(amount_base), 0) AS total FROM costs")
        return round(float(row["total"]) if row else 0.0, 4)

    def costs_by_category(self) -> dict[str, float]:
        rows = self._query(
            "SELECT category, COALESCE(SUM(amount_base),0) AS total FROM costs GROUP BY category"
        )
        return {str(r["category"]): round(float(r["total"]), 4) for r in rows}

    def costs_since(self, since_iso: str) -> float:
        row = self._one(
            "SELECT COALESCE(SUM(amount_base),0) AS total FROM costs WHERE incurred_at >= ?",
            (since_iso,),
        )
        return round(float(row["total"]) if row else 0.0, 4)

    def llm_stats_since(self, since_iso: str) -> dict[str, Any]:
        row = self._one(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(amount_base),0) AS total,
                   MAX(incurred_at) AS last_at
            FROM costs
            WHERE category='llm' AND incurred_at >= ?
            """,
            (since_iso,),
        )
        return {
            "n": int(row["n"]) if row else 0,
            "total": round(float(row["total"]) if row and row["total"] is not None else 0.0, 6),
            "last_at": row["last_at"] if row else None,
        }

    def last_llm_call_at(self) -> Optional[str]:
        row = self._one(
            "SELECT incurred_at FROM costs WHERE category='llm' ORDER BY id DESC LIMIT 1"
        )
        return str(row["incurred_at"]) if row and row.get("incurred_at") else None

    def list_costs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM costs ORDER BY id DESC LIMIT ?", (limit,))

    # -- survival ---------------------------------------------------------
    def agent_life(self) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM agent_life WHERE id=1")

    def init_agent_life(
        self,
        *,
        born_at: str,
        base_currency: str,
        starting_equity: float,
        terminal_threshold: float,
        survival_state: str,
    ) -> dict[str, Any]:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO agent_life (
                    id, born_at, base_currency, starting_equity, terminal_threshold,
                    highest_equity, survival_state, updated_at
                ) VALUES (1,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    born_at, base_currency, starting_equity, terminal_threshold,
                    starting_equity, survival_state, self._now(),
                ),
            )
            self.conn.commit()
        life = self.agent_life()
        assert life is not None
        return life

    def update_agent_life(self, **fields: Any) -> Optional[dict[str, Any]]:
        if not fields:
            return self.agent_life()
        allowed = {
            "highest_equity",
            "survival_state",
            "terminated_at",
            "terminal_reason",
            "terminal_threshold",
            "desired_running",
            "paper_equity",
        }
        sets = [f"{k} = ?" for k in fields if k in allowed]
        params = [fields[k] for k in fields if k in allowed]
        if not sets:
            return self.agent_life()
        sets.append("updated_at = ?")
        params.append(self._now())
        self._write(f"UPDATE agent_life SET {', '.join(sets)} WHERE id = 1", tuple(params))
        return self.agent_life()

    def record_survival_transition(
        self,
        *,
        from_state: str,
        to_state: str,
        equity: float,
        reason: str,
        threshold: Optional[float] = None,
        irreversible: bool = False,
    ) -> int:
        return self._write(
            """
            INSERT INTO survival_transitions (
                created_at, from_state, to_state, equity, threshold, reason, irreversible
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (self._now(), from_state, to_state, equity, threshold, reason, int(irreversible)),
        )

    def list_survival_transitions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM survival_transitions ORDER BY id DESC LIMIT ?", (limit,)
        )

    def record_milestone(self, *, key: str, label: str, equity: float) -> bool:
        """Returns True if this milestone was newly reached."""
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO milestones (created_at, key, label, equity)
                VALUES (?,?,?,?)
                ON CONFLICT(key) DO NOTHING
                """,
                (self._now(), key, label, equity),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def list_milestones(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM milestones ORDER BY equity ASC")

    # -- contract execution ----------------------------------------------
    def record_contract_order(self, payload: dict[str, Any]) -> int:
        return self._write(
            """
            INSERT INTO contract_orders (
                created_at, order_id, idempotency_key, decision_id, venue, ticker,
                side, action, contracts, limit_price, status, reason
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(),
                payload["order_id"],
                payload.get("idempotency_key"),
                payload.get("decision_id"),
                payload.get("venue", "paper"),
                payload["ticker"],
                payload.get("side", "YES"),
                payload.get("action", "BUY"),
                int(payload.get("contracts", 0)),
                payload.get("limit_price"),
                payload.get("status", "FILLED"),
                payload.get("reason"),
            ),
        )

    def order_exists(self, idempotency_key: str) -> bool:
        row = self._one(
            "SELECT COUNT(*) AS n FROM contract_orders WHERE idempotency_key=?",
            (idempotency_key,),
        )
        return bool(row and int(row["n"]) > 0)

    def record_contract_fill(self, payload: dict[str, Any]) -> int:
        return self._write(
            """
            INSERT INTO contract_fills (
                created_at, fill_id, order_id, ticker, side, contracts, price,
                premium, fee, quote_currency, fx_rate, premium_base, fee_base
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(), payload["fill_id"], payload["order_id"], payload["ticker"],
                payload.get("side", "YES"), int(payload["contracts"]), float(payload["price"]),
                float(payload["premium"]), float(payload.get("fee", 0.0)),
                payload.get("quote_currency", "USD"), float(payload.get("fx_rate", 1.0)),
                float(payload["premium_base"]), float(payload.get("fee_base", 0.0)),
            ),
        )

    def upsert_contract_position(self, payload: dict[str, Any]) -> int:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO contract_positions (
                    created_at, updated_at, position_id, decision_id, ticker, event_key,
                    side, contracts, average_price, premium_base, fees_base,
                    max_loss_base, max_gain_base, open, resolved_outcome,
                    settlement_base, realised_pnl_base, closed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(position_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    contracts=excluded.contracts,
                    average_price=excluded.average_price,
                    premium_base=excluded.premium_base,
                    fees_base=excluded.fees_base,
                    max_loss_base=excluded.max_loss_base,
                    max_gain_base=excluded.max_gain_base,
                    open=excluded.open,
                    resolved_outcome=excluded.resolved_outcome,
                    settlement_base=excluded.settlement_base,
                    realised_pnl_base=excluded.realised_pnl_base,
                    closed_at=excluded.closed_at
                """,
                (
                    self._now(), self._now(), payload["position_id"], payload.get("decision_id"),
                    payload["ticker"], payload.get("event_key"), payload.get("side", "YES"),
                    int(payload["contracts"]), float(payload["average_price"]),
                    float(payload["premium_base"]), float(payload.get("fees_base", 0.0)),
                    float(payload["max_loss_base"]), float(payload["max_gain_base"]),
                    int(bool(payload.get("open", True))), payload.get("resolved_outcome"),
                    payload.get("settlement_base"), payload.get("realised_pnl_base"),
                    payload.get("closed_at"),
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM contract_positions WHERE position_id=?", (payload["position_id"],)
            ).fetchone()
            return int(row["id"])

    def open_contract_positions(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM contract_positions WHERE open=1 ORDER BY id")

    def list_contract_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM contract_positions ORDER BY id DESC LIMIT ?", (limit,))

    def exposure_by_event(self, event_key: str) -> float:
        row = self._one(
            "SELECT COALESCE(SUM(max_loss_base),0) AS total FROM contract_positions "
            "WHERE open=1 AND event_key=?",
            (event_key,),
        )
        return round(float(row["total"]) if row else 0.0, 4)

    def exposure_by_ticker(self, ticker: str) -> float:
        row = self._one(
            "SELECT COALESCE(SUM(max_loss_base),0) AS total FROM contract_positions "
            "WHERE open=1 AND ticker=?",
            (ticker,),
        )
        return round(float(row["total"]) if row else 0.0, 4)

    def total_open_exposure(self) -> float:
        row = self._one(
            "SELECT COALESCE(SUM(max_loss_base),0) AS total FROM contract_positions WHERE open=1"
        )
        return round(float(row["total"]) if row else 0.0, 4)

    # -- strategy performance ---------------------------------------------
    def record_strategy_performance(self, payload: dict[str, Any]) -> int:
        return self._write(
            """
            INSERT INTO strategy_performance (
                created_at, strategy, split, trades, wins, losses, win_rate,
                expectancy, gross_pnl, fees, net_pnl, return_pct, max_drawdown,
                brier, average_edge, realised_edge, payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self._now(), payload.get("strategy", "unknown"), payload.get("split"),
                payload.get("trades"), payload.get("wins"), payload.get("losses"),
                payload.get("win_rate"), payload.get("expectancy"), payload.get("gross_pnl"),
                payload.get("fees"), payload.get("net_pnl"), payload.get("return_pct"),
                payload.get("max_drawdown"), payload.get("brier"), payload.get("average_edge"),
                payload.get("realised_edge"), _json(payload.get("payload")),
            ),
        )

    def list_strategy_performance(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM strategy_performance ORDER BY id DESC LIMIT ?", (limit,)
        )
