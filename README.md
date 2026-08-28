# AI-Trader

Paper-only research desk for an AI-assisted trading bot.

**Live trading is disabled in code.** The process will refuse to start in live
mode, reject the Alpaca live API host, and refuse to place orders. Grok never
talks to a broker. The risk engine sits between any future AI decision and
execution.

This repository is the **foundation only**. There is no strategy, no order
routing, and no broker connection yet.

## What this build includes

- Modular Python package (`src/ai_trader`)
- Configuration via environment variables
- SQLite schema for decisions, trades, positions, account snapshots, and events
- Kill switch (file-backed, starts engaged)
- Stub adapters for market data, analysis, Grok, simulated broker, Alpaca paper
- Status dashboard
- Automated tests

## Architecture

```text
Market Data
    ↓
Market / News Analysis
    ↓
Grok AI                 ← proposes BUY / SELL / HOLD only
    ↓
Decision (proposal)
    ↓
Risk Management Engine  ← hard gate; AI cannot skip this
    ↓
Paper Trading Execution ← blocked in this build
    ↓
Trade & Event Database
    ↓
Dashboard
```

Every arrow is a module boundary. Swap a provider later (Alpaca data, another
broker, another model) without rewriting the rest of the desk.

The orchestrator (`src/ai_trader/pipeline/orchestrator.py`) is the only place
allowed to call those modules in sequence. Dashboard buttons cannot place
orders. The AI client cannot place orders. The broker adapters refuse orders
until a later, explicit build step.

## Folder structure

```text
ai-trader/
  src/ai_trader/
    config.py              # env/config, never logs secrets
    safety.py              # live-trading invariant
    kill_switch.py         # halt file
    logging_setup.py
    runtime.py             # wires the process
    types.py
    exceptions.py
    db/                    # SQLite schema + repository
    market_data/           # provider interface + simulated stub
    analysis/              # news/market analysis interface
    ai/                    # Grok adapter (no API calls yet)
    risk/                  # risk engine (rejects everything)
    broker/                # simulated + Alpaca paper stubs
    pipeline/              # orchestrator
    dashboard/             # FastAPI status UI
  tests/
  data/                    # local sqlite + kill switch (gitignored)
  logs/                    # rotating log files (gitignored)
  .env.example
  requirements.txt
  requirements-dev.txt
  pyproject.toml
```

## Setup on a Mac (later)

```bash
cd ai-trader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Fill nothing yet. Leave keys blank until a later step.
```

Run tests:

```bash
PYTHONPATH=src pytest
```

Start the dashboard:

```bash
PYTHONPATH=src python -m ai_trader
```

Other commands: `python -m ai_trader status`, `python -m ai_trader init-db`.

## Environment variables

See `.env.example`. Only two trading modes are accepted: `simulate` and `paper`.
`live` (and anything containing live/prod/real) is rejected at startup.

| Variable | Purpose |
| --- | --- |
| `TRADING_MODE` | `simulate` or `paper` |
| `XAI_API_KEY` | Grok / xAI — unused in this build |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Paper keys — unused in this build |
| `ALPACA_BASE_URL` | Must remain `https://paper-api.alpaca.markets` |
| `KILL_SWITCH_ENGAGED` | Defaults to `true` |

Never commit `.env`. The dashboard never receives secrets.

## How Grok will connect (later)

`src/ai_trader/ai/grok_client.py` is the analysis adapter.

1. Read `XAI_API_KEY` on the server only.
2. `POST https://api.x.ai/v1/chat/completions` with model `grok-4.5`.
3. Parse a structured BUY / SELL / HOLD proposal.
4. Persist it in `ai_decisions`.
5. Hand it to the risk engine.

The client will not run on a timer until we add that deliberately. It will
never call a broker. This foundation does not send any xAI requests.

## How Alpaca paper trading will connect (later)

`src/ai_trader/broker/alpaca_paper.py` is the execution adapter.

1. Authenticate against `https://paper-api.alpaca.markets` only.
2. Accept an order only if the kill switch is clear **and** the risk engine
   approved it.
3. Persist the result in `trades` / `positions` / `account_snapshots`.

The live host `https://api.alpaca.markets` is rejected in `safety.py`. This
foundation does not open a network connection to Alpaca and does not install
an Alpaca SDK.

## How live trading is prevented

1. `LIVE_TRADING_ALLOWED = False` is hardcoded. Env vars cannot flip it.
2. Config startup calls `assert_safe_to_run()`.
3. Forbidden modes: `live`, `prod`, `production`, `real`, `cash`.
4. Alpaca live URL is blocked.
5. Kill switch starts engaged.
6. Risk engine rejects every order.
7. Both brokers raise if `submit()` is called.
8. Orchestrator `place_order()` raises.
9. Dashboard `POST /api/orders` returns 403.

There is no live-trading code path to “turn on by mistake”.

## Kill switch

File: `data/KILL_SWITCH`. If the file exists, the pipeline is halted.

The dashboard can engage / disengage it. Disengaging still does not allow
orders.

## What is intentionally not here

- No strategy
- No scheduled trading loop
- No Grok prompts in production use
- No Alpaca SDK
- No live broker support
- No real-money account handling

Next steps will be added one module at a time.
