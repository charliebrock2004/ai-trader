# AI-Trader

Paper-only research desk for an AI-assisted trading bot.

**Live trading is disabled in code.** The process will refuse to start in live
mode, reject the Alpaca live API host, and refuse to place orders. Grok never
talks to a broker. The risk engine sits between any AI decision and execution.

Start a paper session from the home screen: £100 simulated account, BTC-USD
5-minute public candles, Grok proposing BUY / SELL / HOLD. Stop blocks new
trades. No real money. No live broker.

## What this build includes

- Modular Python package (`src/ai_trader`)
- Configuration via environment variables
- SQLite schema for decisions, trades, positions, account snapshots, and events
- Kill switch (file-backed, starts engaged)
- Public Coinbase candles for BTC-USD / ETH-USD (completed bars only)
- Fixture Grok (default) plus gated real Grok paper analysis (`grok-4.6`)
- Internal paper simulator (fills/positions/SL-TP). Broker.submit is never called.
- Continuous paper session: sequential walk, no look-ahead, poll for new bars
- Strategy benchmark: buy-and-hold, SMA 10/20, seeded random, and Grok on the same paper book
- Simple trading home (balance, P&L, Grok status, Start / Stop, Performance)
- Automated tests

## Architecture

```text
Market Data              ← simulated or public Coinbase
    ↓
Market / News Analysis
    ↓
Grok AI                  ← proposes BUY / SELL / HOLD only (fixture default)
    ↓
Decision validation
    ↓
Risk Management Engine   ← hard gate; AI cannot skip this
    ↓
Paper Simulator          ← internal fills only. Broker never called.
    ↓
Trade & Event Database
    ↓
Dashboard
```

Every arrow is a module boundary. Swap a provider later (Alpaca data, another
broker, another model) without rewriting the rest of the desk.

The orchestrator (`src/ai_trader/pipeline/orchestrator.py`) is the only place
allowed to call those modules in sequence. Dashboard buttons cannot place
orders. The AI client cannot place orders. The broker adapters refuse orders.

## Folder structure

```text
ai-trader/
  src/ai_trader/           # Python paper engine
  src/components/          # trading home, performance, system
  src/routes/              # app pages + paper-session API
  tests/
  data/                    # local sqlite + kill switch (gitignored)
  logs/                    # rotating log files (gitignored)
  .env.example
  requirements.txt
  requirements-dev.txt
  pyproject.toml
```

## Safety

- `LIVE_TRADING_ALLOWED = False` is hardcoded. There is no env override.
- Allowed modes: `simulate` and `paper`. Anything else refuses to start.
- `https://api.alpaca.markets` is blocked. Paper host only.
- Kill switch starts engaged.
- Risk engine `allow_orders=False`.
- Grok paper analysis is opt-in (`GROK_PAPER_ANALYSIS=true`) and still cannot
  place broker orders.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python3 -m pytest
```

To run the paper desk UI:

```bash
npm install
npm run dev
```

Grok paper analysis stays off until you set `GROK_PAPER_ANALYSIS=true` and an
`XAI_API_KEY` in a local `.env` file. Never commit that file.

## What is still not in this repo

- Live trading
- Real-money brokers
- Unrestricted Grok control of orders
