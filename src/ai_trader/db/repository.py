"""Persistence helpers. All writes are explicit; nothing auto-trades."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ai_trader.db.schema import initialise_database, list_tables
from ai_trader.types import CandleSeries, MarketAnalysis, utc_now_iso


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = initialise_database(path)

    def close(self) -> None:
        self.conn.close()

    def table_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name in list_tables(self.conn):
            row = self.conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()
            counts[name] = int(row["n"])
        return counts

    def record_event(
        self,
        *,
        level: str,
        source: str,
        event_type: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO events (created_at, level, source, event_type, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                level.upper(),
                source,
                event_type,
                message,
                json.dumps(details) if details else None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_decision(
        self,
        *,
        symbol: Optional[str],
        action: str,
        rationale: str,
        model: str,
        status: str = "recorded",
        confidence: Optional[float] = None,
        raw_response: str = "",
        market_snapshot_json: str = "",
        prompt_hash: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO ai_decisions (
                created_at, symbol, action, confidence, rationale, model,
                prompt_hash, raw_response, market_snapshot_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                symbol,
                action,
                confidence,
                rationale,
                model,
                prompt_hash,
                raw_response,
                market_snapshot_json,
                status,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_decision(self, symbol: Optional[str] = None) -> Optional[dict[str, Any]]:
        if symbol:
            row = self.conn.execute(
                """
                SELECT * FROM ai_decisions
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        return {
            "id": item["id"],
            "symbol": item["symbol"],
            "action": item["action"],
            "confidence": item["confidence"],
            "reasoning": item["rationale"],
            "timestamp": item["created_at"],
            "analysis_ref": item["prompt_hash"] or "",
            "model": item["model"],
            "status": item["status"],
        }

    def record_blocked_trade(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        reason: str,
        decision_id: Optional[int] = None,
        broker: str = "none",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO trades (
                created_at, decision_id, symbol, side, qty, intended_price,
                status, broker, broker_order_id, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                decision_id,
                symbol,
                side,
                qty,
                None,
                "blocked",
                broker,
                None,
                reason,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_positions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM positions ORDER BY symbol"
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_account(self) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def record_account_snapshot(
        self,
        *,
        mode: str,
        source: str,
        equity: Optional[float] = None,
        cash: Optional[float] = None,
        buying_power: Optional[float] = None,
        portfolio_value: Optional[float] = None,
        raw: Optional[dict[str, Any]] = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO account_snapshots (
                created_at, equity, cash, buying_power, portfolio_value,
                mode, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                equity,
                cash,
                buying_power,
                portfolio_value,
                mode,
                source,
                json.dumps(raw) if raw else None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_series(self, series: CandleSeries) -> int:
        payload = json.dumps([c.to_dict() for c in series.candles])
        cur = self.conn.execute(
            """
            INSERT INTO market_series (
                created_at, symbol, timeframe, scenario, seed, source,
                bar_count, candles_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                series.symbol,
                series.timeframe,
                series.scenario,
                series.seed,
                series.source,
                len(series.candles),
                payload,
            ),
        )
        series_id = int(cur.lastrowid)
        rows = [
            (
                series_id,
                series.symbol,
                series.timeframe,
                candle.timestamp,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for candle in series.candles
        ]
        self.conn.executemany(
            """
            INSERT INTO market_candles (
                series_id, symbol, timeframe, ts, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return series_id

    def latest_series(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self.conn.execute(
                """
                SELECT * FROM market_series
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT s.* FROM market_series s
                JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_series
                    GROUP BY symbol
                ) latest ON latest.max_id = s.id
                ORDER BY s.symbol
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["candles"] = json.loads(item.pop("candles_json"))
            result.append(item)
        return result

    def list_candles(
        self, symbol: str, *, timeframe: Optional[str] = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        latest = self.latest_series(symbol)
        if not latest:
            return []
        series = latest[0]
        if timeframe and series["timeframe"] != timeframe:
            return []
        return list(series["candles"])[:limit]

    def save_analysis(self, analysis: MarketAnalysis) -> int:
        payload = json.dumps(analysis.to_dict())
        cur = self.conn.execute(
            """
            INSERT INTO market_analysis (
                created_at, symbol, timeframe, scenario, as_of, bar_count,
                trend, current_price, analysis_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                analysis.symbol,
                analysis.timeframe,
                analysis.scenario,
                analysis.as_of,
                analysis.bar_count,
                analysis.trend,
                analysis.current_price,
                payload,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_analysis(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self.conn.execute(
                """
                SELECT * FROM market_analysis
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT a.* FROM market_analysis a
                JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM market_analysis
                    GROUP BY symbol
                ) latest ON latest.max_id = a.id
                ORDER BY a.symbol
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            body = json.loads(item.pop("analysis_json"))
            body["id"] = item["id"]
            body["created_at"] = item["created_at"]
            result.append(body)
        return result

    def persist_paper_run(self, report: dict[str, Any]) -> None:
        created = utc_now_iso()
        for order in report.get("orders") or []:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO paper_orders (
                    created_at, order_id, symbol, side, quantity, requested_price,
                    filled_price, stop_loss, take_profit, status, reason, source, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    order["order_id"],
                    order["symbol"],
                    order["side"],
                    order["quantity"],
                    order.get("requested_price"),
                    order.get("filled_price"),
                    order.get("stop_loss"),
                    order.get("take_profit"),
                    order["status"],
                    order.get("reason"),
                    order.get("source"),
                    json.dumps(order),
                ),
            )
        for fill in report.get("fills") or []:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO paper_fills (
                    created_at, fill_id, order_id, symbol, side, quantity, price,
                    reason, spread, slippage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    fill["fill_id"],
                    fill["order_id"],
                    fill["symbol"],
                    fill["side"],
                    fill["quantity"],
                    fill["price"],
                    fill["reason"],
                    fill.get("spread"),
                    fill.get("slippage"),
                ),
            )
        self.conn.execute("DELETE FROM paper_positions")
        for pos in (report.get("positions") or []) + (report.get("closed_positions") or []):
            self.conn.execute(
                """
                INSERT INTO paper_positions (
                    updated_at, symbol, side, quantity, average_entry, current_price,
                    stop_loss, take_profit, unrealised_pnl, realised_pnl, open, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created,
                    pos["symbol"],
                    pos.get("side", "LONG"),
                    pos["quantity"],
                    pos.get("average_entry"),
                    pos.get("current_price"),
                    pos.get("stop_loss"),
                    pos.get("take_profit"),
                    pos.get("unrealised_pnl"),
                    pos.get("realised_pnl"),
                    1 if pos.get("open") else 0,
                    json.dumps(pos),
                ),
            )
        perf = report.get("performance") or {}
        self.conn.execute(
            """
            INSERT INTO performance_snapshots (
                created_at, symbol, source, total_trades, winning_trades, losing_trades,
                win_rate, profit_factor, average_win, average_loss, maximum_drawdown,
                return_pct, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                report.get("symbol"),
                report.get("signal_source"),
                perf.get("total_trades"),
                perf.get("winning_trades"),
                perf.get("losing_trades"),
                perf.get("win_rate"),
                perf.get("profit_factor"),
                perf.get("average_win"),
                perf.get("average_loss"),
                perf.get("maximum_drawdown"),
                perf.get("return_pct"),
                json.dumps(perf),
            ),
        )
        self.conn.commit()

    def list_paper_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def list_paper_fills(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM paper_fills ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def list_paper_positions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM paper_positions ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    def latest_performance(self) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM performance_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def health(self) -> dict[str, Any]:

        return {
            "path": str(self.path),
            "tables": list_tables(self.conn),
            "counts": self.table_counts(),
        }
