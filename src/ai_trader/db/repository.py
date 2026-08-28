"""Persistence helpers. All writes are explicit; nothing auto-trades."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ai_trader.db.schema import initialise_database, list_tables
from ai_trader.types import utc_now_iso


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

    def health(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "tables": list_tables(self.conn),
            "counts": self.table_counts(),
        }
