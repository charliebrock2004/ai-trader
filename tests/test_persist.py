from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ai_trader.persist import (
    build_snapshot,
    dump_sqlite,
    restore_if_needed,
    should_restore,
)
from ai_trader.safety import LIVE_TRADING_ALLOWED


def test_should_restore_is_off_in_tests(isolated_env) -> None:
    assert should_restore() is False


def test_snapshot_round_trip_does_not_reset_equity(isolated_env, monkeypatch) -> None:
    db = isolated_env / "ai_trader.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE agent_life (
            id INTEGER PRIMARY KEY,
            desired_running INTEGER NOT NULL DEFAULT 0,
            paper_equity REAL,
            survival_state TEXT,
            terminated_at TEXT
        );
        INSERT INTO agent_life (id, desired_running, paper_equity, survival_state)
        VALUES (1, 1, 87.5, 'CAUTION');
        """
    )
    conn.commit()
    conn.close()

    snapshot = build_snapshot(db)
    assert snapshot["live"] is False
    assert snapshot["live_trading_allowed"] is False
    assert snapshot["paper_equity"] == 87.5
    assert snapshot["desired_running"] is True
    assert "INSERT INTO \"agent_life\"" in snapshot["sql"] or "INSERT INTO agent_life" in snapshot["sql"]
    assert LIVE_TRADING_ALLOWED is False

    copy = isolated_env / "restored.db"
    monkeypatch.setenv("PERSIST_RESTORE", "true")
    monkeypatch.setenv("RENDER", "true")

    # Restore uses GitHub. Force a local apply through dump_sqlite instead.
    sql = dump_sqlite(db)
    conn = sqlite3.connect(str(copy))
    conn.executescript(sql)
    conn.commit()
    row = conn.execute("SELECT paper_equity, desired_running FROM agent_life").fetchone()
    conn.close()
    assert row[0] == 87.5
    assert row[1] == 1

    # A populated local db is not clobbered even when restore is enabled.
    monkeypatch.setattr("ai_trader.persist.fetch_checkpoint", lambda: {"sql": "SELECT 1;", "live": False})
    result = restore_if_needed(copy)
    assert result["restored"] is False
    conn = sqlite3.connect(str(copy))
    assert conn.execute("SELECT paper_equity FROM agent_life").fetchone()[0] == 87.5
    conn.close()


def test_empty_db_restores_checkpoint(isolated_env, monkeypatch) -> None:
    empty = isolated_env / "empty.db"
    source = isolated_env / "source.db"
    conn = sqlite3.connect(str(source))
    conn.executescript(
        """
        CREATE TABLE agent_life (
            id INTEGER PRIMARY KEY,
            desired_running INTEGER NOT NULL DEFAULT 0,
            paper_equity REAL,
            survival_state TEXT,
            terminated_at TEXT
        );
        INSERT INTO agent_life (id, desired_running, paper_equity, survival_state)
        VALUES (1, 0, 64.0, 'DEFENSIVE');
        """
    )
    conn.commit()
    conn.close()
    snapshot = {
        "sql": dump_sqlite(source),
        "live": False,
        "latch": None,
        "updated_at": "2026-09-03T00:00:00Z",
        "paper_equity": 64.0,
    }
    monkeypatch.setenv("PERSIST_RESTORE", "true")
    monkeypatch.setattr("ai_trader.persist.fetch_checkpoint", lambda: snapshot)
    result = restore_if_needed(empty)
    assert result["restored"] is True
    assert result["durable"] is True
    conn = sqlite3.connect(str(empty))
    assert conn.execute("SELECT paper_equity FROM agent_life").fetchone()[0] == 64.0
    conn.close()
    assert json.loads(json.dumps(result))["kind"] == "github-checkpoint"
