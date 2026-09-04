-- AI-Trader agent schema: the permanent audit trail.
--
-- This is the part of the system that makes results believable. Every decision
-- the agent reaches is written here, including the ones where it did nothing:
-- HOLDs, rejected opportunities, and the reason each was rejected.
--
-- All timestamps are ISO-8601 UTC strings. All money is in the agent's base
-- accounting currency unless a column says otherwise.

-- ---------------------------------------------------------------------------
-- Agent identity and life
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_life (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    born_at TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    terminal_threshold REAL NOT NULL,
    highest_equity REAL NOT NULL,
    survival_state TEXT NOT NULL,
    terminated_at TEXT,
    terminal_reason TEXT,
    desired_running INTEGER NOT NULL DEFAULT 0,
    paper_equity REAL,
    last_processed_candle_ts TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS survival_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    equity REAL NOT NULL,
    threshold REAL,
    reason TEXT NOT NULL,
    irreversible INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    equity REAL NOT NULL
);

-- ---------------------------------------------------------------------------
-- Markets and their observed state
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    venue TEXT NOT NULL,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'binary' | 'spot'
    question TEXT,
    event_key TEXT,
    resolution_source TEXT,
    resolution_time TEXT,
    settlement_rules TEXT,
    tick_size REAL,
    min_order REAL,
    max_order REAL,
    fee_model TEXT,
    quote_currency TEXT NOT NULL DEFAULT 'USD',
    raw_json TEXT,
    UNIQUE (venue, ticker)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    market_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    mid REAL,
    spread REAL,
    top_depth REAL,
    total_depth REAL,
    book_json TEXT,
    source TEXT NOT NULL,
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

-- ---------------------------------------------------------------------------
-- Official data: the objective input the whole strategy rests on
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS official_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    series_key TEXT NOT NULL,           -- e.g. 'BLS:CUUR0000SA0'
    release_key TEXT NOT NULL,          -- e.g. 'CPI:2026-03'
    source TEXT NOT NULL,
    scheduled_at TEXT,
    published_at TEXT,
    observed_at TEXT NOT NULL,
    value REAL,
    unit TEXT,
    status TEXT NOT NULL,               -- pending|verified|conflict|unavailable
    verified INTEGER NOT NULL DEFAULT 0,
    verification_method TEXT,
    second_read REAL,
    notes TEXT,
    raw_json TEXT,
    UNIQUE (series_key, release_key, observed_at)
);

-- ---------------------------------------------------------------------------
-- Opportunities: every candidate considered, selected or not
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    market_id INTEGER,
    ticker TEXT NOT NULL,
    event_key TEXT,
    official_data_id INTEGER,
    side TEXT NOT NULL DEFAULT 'YES',
    model_probability REAL,
    market_probability REAL,
    fee_cost REAL,
    spread_cost REAL,
    gross_edge REAL,
    net_edge REAL,
    liquidity REAL,
    time_to_resolution_seconds REAL,
    data_confidence REAL,
    resolution_confidence REAL,
    rank_score REAL,
    selected INTEGER NOT NULL DEFAULT 0,
    reject_reason TEXT,
    inputs_json TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(id),
    FOREIGN KEY (official_data_id) REFERENCES official_data(id)
);

-- ---------------------------------------------------------------------------
-- Decisions: the black box
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'binary' | 'spot'
    ticker TEXT,
    market_id INTEGER,
    event_key TEXT,
    opportunity_id INTEGER,

    -- what the deterministic layer computed
    model_probability REAL,
    market_probability REAL,
    gross_edge REAL,
    net_edge REAL,
    fees REAL,
    spread REAL,
    liquidity REAL,

    -- what the analyst said
    ai_model TEXT,
    ai_action TEXT,
    ai_confidence REAL,
    ai_bull TEXT,
    ai_bear TEXT,
    ai_invalidators TEXT,
    ai_raw TEXT,
    ai_validated INTEGER NOT NULL DEFAULT 0,
    ai_failure TEXT,

    -- what the guardian said
    proposed_action TEXT,
    policy_action TEXT,
    policy_reason TEXT,
    survival_state TEXT,
    risk_multiplier REAL,

    -- what risk said
    risk_approved INTEGER NOT NULL DEFAULT 0,
    risk_reason TEXT,
    risk_json TEXT,

    -- what actually happened
    final_action TEXT NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    order_ref TEXT,
    stage TEXT,
    equity_before REAL,
    cash_before REAL,
    base_currency TEXT,
    notes TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(id),
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
);

CREATE TABLE IF NOT EXISTS decision_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'market'|'official'|'account'|'derived'|'config'
    value_json TEXT NOT NULL,
    source TEXT,
    as_of TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

-- ---------------------------------------------------------------------------
-- Prediction-market execution
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contract_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    order_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    decision_id INTEGER,
    venue TEXT NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,                 -- YES | NO
    action TEXT NOT NULL,               -- BUY | SELL
    contracts INTEGER NOT NULL,
    limit_price REAL,
    status TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS contract_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    fill_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    price REAL NOT NULL,
    premium REAL NOT NULL,
    fee REAL NOT NULL,
    quote_currency TEXT NOT NULL,
    fx_rate REAL NOT NULL,
    premium_base REAL NOT NULL,
    fee_base REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position_id TEXT NOT NULL UNIQUE,
    decision_id INTEGER,
    ticker TEXT NOT NULL,
    event_key TEXT,
    side TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    average_price REAL NOT NULL,
    premium_base REAL NOT NULL,
    fees_base REAL NOT NULL,
    max_loss_base REAL NOT NULL,
    max_gain_base REAL NOT NULL,
    open INTEGER NOT NULL DEFAULT 1,
    resolved_outcome INTEGER,
    settlement_base REAL,
    realised_pnl_base REAL,
    closed_at TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

-- ---------------------------------------------------------------------------
-- Outcomes and calibration
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    decision_id INTEGER NOT NULL,
    ticker TEXT,
    event_key TEXT,
    predicted_probability REAL NOT NULL,
    market_probability REAL,
    resolved_outcome INTEGER NOT NULL,   -- 1 = YES happened, 0 = NO
    resolved_at TEXT NOT NULL,
    resolution_source TEXT,
    realised_pnl_base REAL,
    predicted_edge REAL,
    realised_edge REAL,
    brier REAL NOT NULL,
    correct INTEGER NOT NULL,
    notes TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id),
    UNIQUE (decision_id)
);

-- ---------------------------------------------------------------------------
-- Operating costs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    incurred_at TEXT NOT NULL,
    category TEXT NOT NULL,             -- llm|hosting|data|fees|other
    description TEXT NOT NULL,
    amount_base REAL NOT NULL,
    currency TEXT NOT NULL,
    units REAL,
    unit_name TEXT,
    reference TEXT
);

-- ---------------------------------------------------------------------------
-- Strategy performance snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    split TEXT,
    trades INTEGER,
    wins INTEGER,
    losses INTEGER,
    win_rate REAL,
    expectancy REAL,
    gross_pnl REAL,
    fees REAL,
    net_pnl REAL,
    return_pct REAL,
    max_drawdown REAL,
    brier REAL,
    average_edge REAL,
    realised_edge REAL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker, created_at);
CREATE INDEX IF NOT EXISTS idx_decision_inputs ON decision_inputs(decision_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_cycle ON opportunities(cycle_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_selected ON opportunities(selected, created_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_market ON market_snapshots(market_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_official_release ON official_data(series_key, release_key);
CREATE INDEX IF NOT EXISTS idx_outcomes_created ON outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_costs_created ON costs(incurred_at);
CREATE INDEX IF NOT EXISTS idx_contract_positions_open ON contract_positions(open, ticker);
CREATE INDEX IF NOT EXISTS idx_survival_created ON survival_transitions(created_at);
