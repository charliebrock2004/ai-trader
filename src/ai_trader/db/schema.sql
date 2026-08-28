-- AI-Trader SQLite schema (foundation)
-- All timestamps are ISO-8601 UTC strings.

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

CREATE INDEX IF NOT EXISTS idx_decisions_created ON ai_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
