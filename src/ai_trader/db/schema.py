"""SQLite schema bootstrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT,
    action TEXT NOT NULL,
    confidence REAL,
    rationale TEXT,
    model TEXT,
    prompt_hash TEXT,
    raw_response TEXT,
    market_snapshot_json TEXT,
    status TEXT NOT NULL DEFAULT 'recorded'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    decision_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL,
    intended_price REAL,
    status TEXT NOT NULL,
    broker TEXT,
    broker_order_id TEXT,
    reason TEXT,
    FOREIGN KEY (decision_id) REFERENCES ai_decisions(id)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at TEXT NOT NULL,
    symbol TEXT NOT NULL UNIQUE,
    qty REAL NOT NULL DEFAULT 0,
    avg_price REAL,
    market_value REAL,
    unrealized_pl REAL,
    source TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    equity REAL,
    cash REAL,
    buying_power REAL,
    portfolio_value REAL,
    mode TEXT NOT NULL,
    source TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS market_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    scenario TEXT NOT NULL,
    seed INTEGER NOT NULL,
    source TEXT NOT NULL,
    bar_count INTEGER NOT NULL,
    candles_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    FOREIGN KEY (series_id) REFERENCES market_series(id)
);

CREATE TABLE IF NOT EXISTS market_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    scenario TEXT,
    as_of TEXT NOT NULL,
    bar_count INTEGER NOT NULL,
    trend TEXT NOT NULL,
    current_price REAL,
    analysis_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_created ON ai_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_market_series_symbol ON market_series(symbol, timeframe, created_at);
CREATE INDEX IF NOT EXISTS idx_market_candles_symbol ON market_candles(symbol, timeframe, ts);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    requested_price REAL,
    filled_price REAL,
    stop_loss REAL,
    take_profit REAL,
    status TEXT NOT NULL,
    reason TEXT,
    source TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    fill_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    reason TEXT NOT NULL,
    spread REAL,
    slippage REAL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    average_entry REAL,
    current_price REAL,
    stop_loss REAL,
    take_profit REAL,
    unrealised_pnl REAL,
    realised_pnl REAL,
    open INTEGER NOT NULL,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT,
    source TEXT,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    average_win REAL,
    average_loss REAL,
    maximum_drawdown REAL,
    return_pct REAL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_created ON paper_orders(created_at);
CREATE INDEX IF NOT EXISTS idx_paper_fills_created ON paper_fills(created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_symbol ON market_analysis(symbol, created_at);
"""

REQUIRED_TABLES = (
    "ai_decisions",
    "trades",
    "positions",
    "account_snapshots",
    "events",
    "market_series",
    "market_candles",
    "market_analysis",
    "paper_orders",
    "paper_fills",
    "paper_positions",
    "performance_snapshots",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


AGENT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_agent.sql"


def agent_schema_sql() -> str:
    return AGENT_SCHEMA_PATH.read_text(encoding="utf-8")


def initialise_database(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(agent_schema_sql())
    conn.commit()
    return conn


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
