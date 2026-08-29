# AI-Trader

Paper-only research desk for an AI-assisted trading bot.

**Live trading is disabled in code.** There is no supported live mode. The
process refuses to start in live mode, rejects the Alpaca live API host, and
never places broker orders. Grok only proposes BUY / SELL / HOLD. The risk
engine sits between that proposal and any paper fill.

Start a paper session from the home screen: **£100** simulated account,
**BTC-USD** 5-minute public Coinbase candles, Grok proposing BUY / SELL / HOLD.
Stop blocks new trades. No real money. No live broker.

| | |
|---|---|
| GitHub | [charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader) |
| Branch | `main` |
| Package version | `0.1.0` (`pyproject.toml`) |
| Account | £100 paper, GBP |
| Default session | BTC-USD, 5m, public Coinbase, continuous |
| Live trading | `LIVE_TRADING_ALLOWED = False` |

This repository is **private**. Do not put API keys in it.

---

## What it is

AI-Trader is a modular paper-trading system:

- A **Python engine** (`src/ai_trader`) walks candles, asks Grok (or a fixture)
  for a decision, sizes through risk, and fills an internal paper ledger.
- A **web desk** (TanStack Start / React) shows balance, today’s P&L, Grok
  status, the current BUY / SELL / HOLD, the open position, Start, Stop, and
  Performance. Advanced pipeline details live under System.

It is **not** a live trading platform. Alpaca live is blocked. Broker `submit`
is disabled on the generic path. The Start button never talks to a live
exchange.

---

## How the application works

1. The web app boots (`npm run dev`). Vite listens for the UI.
2. A Vite plugin starts a Python **stdio worker** (`python3 -m ai_trader rpc`).
   There is no second HTTP server for paper trading.
3. The user presses **Start**.
4. The browser POSTs `/api/paper-session/start`.
5. The API route sends a JSON-line `start` command to the Python worker.
6. The orchestrator starts a continuous paper session:
   public Coinbase BTC-USD 5m candles → technical analysis → Grok (if enabled)
   → risk → internal paper fills → SQLite / in-memory session status.
7. The UI polls `/api/paper-session` while Grok is RUNNING.
8. **Stop** sends `/api/paper-session/stop`. New paper trades are blocked.

Grok is **user-initiated** (Start). It is not called on page load. Frequency is
capped (warmup 8 bars, then every 8 completed 5-minute bars).

---

## Pipeline

```text
Market data          Coinbase public candles (BTC-USD / ETH-USD) or simulated
        ↓
Analysis             TechnicalAnalyst — SMAs, trend, returns. Read-only.
        ↓
Grok / fixture       ProposedDecision: BUY / SELL / HOLD only
        ↓
Decision validation  JSON schema. Invalid → HOLD
        ↓
Risk engine          Hard gate. AI cannot skip or change limits.
        ↓
Paper execution      Internal ledger. Next-bar open ± spread/slip. No broker.
        ↓
SQLite + dashboard   Decisions, fills, events. Home screen status.
```

Every arrow is a module boundary. The orchestrator
(`src/ai_trader/pipeline/orchestrator.py`) is the only place allowed to call
those modules in sequence.

Dashboard buttons cannot place broker orders. The AI client cannot place
orders. `SimulatedBroker.submit` raises. Alpaca paper is not used unless
paper keys exist **and** mode is `paper`; the current Start path still fills
the internal simulator and reports `broker: NOT USED` when Alpaca is not
configured.

---

## How to run

Python 3.10+ and Node 22.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python3 -m pytest
npm install
npm run dev
```

Copy `.env.example` to `.env` on your machine. **Never commit `.env`.**

Grok paper analysis stays off until you set both:

```text
GROK_PAPER_ANALYSIS=true
XAI_API_KEY=...
```

in a local `.env`. That flag enables **paper analysis only**. It cannot enable
live trading.

Python-only FastAPI desk (used by tests, not the main preview):

```bash
PYTHONPATH=src python3 -m ai_trader dashboard
```

Paper-engine stdio worker (started automatically by the web app):

```bash
PYTHONPATH=src python3 -m ai_trader rpc
```

---

## How the preview / backend starts

| Piece | What it does |
|---|---|
| [`startup.sh`](startup.sh) | Sandbox revive: if the web app is down, starts `npm run dev`. Does not start a second Python HTTP server. |
| [`package.json`](package.json) `dev` | `vite dev --host 0.0.0.0 --port 8080` |
| [`vite.config.ts`](vite.config.ts) `paperEnginePlugin` | On Vite boot, calls `ensurePaperWorker()` so the paper engine starts with the app. |
| [`src/lib/paper-engine.server.ts`](src/lib/paper-engine.server.ts) | Spawns `python3 -u -m ai_trader rpc` and talks JSON lines over stdin/stdout. |
| [`src/ai_trader/rpc.py`](src/ai_trader/rpc.py) | Handles `health`, `status`, `start`, `stop`. Never a broker. |

If the worker cannot start, Start returns a clear paper-engine error. The UI
does not pretend a session began.

---

## Where the Start button connects

1. [`src/components/trading-home.tsx`](src/components/trading-home.tsx) — `Start` POSTs `/api/paper-session/start` with `{ symbol: "BTC-USD", source: "public", bars: 24, timeframe: "5m", grok_frequency: 8, warmup: 8, continuous: true }`.
2. [`src/routes/api/paper-session.start.ts`](src/routes/api/paper-session.start.ts) — server handler, same defaults.
3. [`src/lib/paper-engine.server.ts`](src/lib/paper-engine.server.ts) — `paperEngineCommand("start", payload)`.
4. [`src/ai_trader/rpc.py`](src/ai_trader/rpc.py) — `handle({ cmd: "start" })`.
5. [`src/ai_trader/pipeline/orchestrator.py`](src/ai_trader/pipeline/orchestrator.py) — `start_paper_session(...)`.
6. [`src/ai_trader/session/runner.py`](src/ai_trader/session/runner.py) — continuous walk on completed candles only.

Stop is the same chain with `/api/paper-session/stop`.

---

## Where the important pieces live

| Concern | Path |
|---|---|
| Coinbase candle adapter | `src/ai_trader/market_data/public.py` (`PublicCryptoFeed`, GET `https://api.exchange.coinbase.com/products/{BTC-USD\|ETH-USD}/candles`) |
| Simulated market data | `src/ai_trader/market_data/simulated.py`, `generator.py` |
| Analysis | `src/ai_trader/analysis/technical.py`, `indicators.py` |
| Grok | `src/ai_trader/ai/grok_client.py` (`GrokAnalyst`, `grok-4.6`, POST `https://api.x.ai/v1/chat/completions`) |
| Fixture Grok | `src/ai_trader/ai/fixture.py` |
| Grok payload / schema | `src/ai_trader/ai/payload.py`, `validate.py` |
| Paper account (£100) | `src/ai_trader/account/simulated.py` (`STARTING_CASH = 100.00`) |
| Paper ledger / fills | `src/ai_trader/paper/ledger.py`, `simulator.py`, `execution.py` |
| Risk engine | `src/ai_trader/risk/engine.py`, `limits.py` |
| Kill switch | `src/ai_trader/kill_switch.py` (file-backed, default engaged) |
| Live-trading invariant | `src/ai_trader/safety.py` (`LIVE_TRADING_ALLOWED = False`) |
| Event / trade logging | `src/ai_trader/db/repository.py`, `schema.sql` |
| Config | `src/ai_trader/config.py`, `.env.example` |
| Alpaca **paper** adapter (not used by Start when keys are absent) | `src/ai_trader/broker/alpaca_paper.py` — live host blocked |
| Tests | `tests/` |
| Deployment config | `vite.config.ts` (Vercel / Nitro preset), `package.json`, `startup.sh` |

Full call graph: [ARCHITECTURE.md](ARCHITECTURE.md).  
How to change the code safely: [DEVELOPMENT.md](DEVELOPMENT.md).

---

## Exactly how live trading is prevented

1. **`LIVE_TRADING_ALLOWED = False`** in `src/ai_trader/safety.py`. There is no
   environment override. Setting it to `True` is forbidden.
2. Allowed modes are only `simulate` and `paper`. Tokens like `live`, `prod`,
   `real`, `cash` refuse to start.
3. **`https://api.alpaca.markets` is blocked.** Only
   `https://paper-api.alpaca.markets` is accepted as an Alpaca URL.
4. Risk engine is constructed with **`allow_orders=False`**.
5. `SimulatedBroker.submit` always raises `OrderPlacementDisabledError`.
6. Paper session **traps** `broker.submit` unless Alpaca paper is fully
   configured. Start still reports `broker: NOT USED` without those keys.
7. `GrokAnalyst` refuses any URL containing `alpaca` and does not send tools.
8. Kill switch starts **engaged**. Disengaging it does not enable live trading.
9. Tests in `tests/test_safety.py` and `tests/test_safety_audit.py` fail the
   build if the live flag or live host leaks into source.

---

## Safety limits (paper)

From `src/ai_trader/risk/limits.py`:

- Starting cash £100
- Max 2% / £2 risk per trade
- Max 2 open positions
- 5% daily loss halt
- 10 trades / day
- No leverage
- 2% stop, 2R take-profit
- Long-only, cash only

---

## Tests

```bash
PYTHONPATH=src python3 -m pytest
```

Do not commit if this fails.

---

## What is still not in this repo

- Live trading
- Real-money brokers
- Unrestricted Grok control of orders
- Alpaca live

---

## AI ASSISTANT DEVELOPMENT GUIDE

This section is for ChatGPT, Claude, Grok, or any other assistant cloning
[charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader).

**Repository:** `charliebrock2004/ai-trader`  
**Branch:** `main`  
**Package version:** `0.1.0`  
**Documented commit:** `4957582acb414adbe6580854a0e9167f8f034e17` on `main` (architecture/docs snapshot). Prefer the tip of `main` if newer.

### Read first (in this order)

1. [DEVELOPMENT.md](DEVELOPMENT.md) — rules. Do not skip.
2. [ARCHITECTURE.md](ARCHITECTURE.md) — module map.
3. `src/ai_trader/safety.py` — live trading must stay False.
4. `src/ai_trader/pipeline/orchestrator.py` — the only legal pipeline wiring.
5. `src/components/trading-home.tsx` — Start / Stop UI (do not redesign unless asked).
6. `src/lib/paper-engine.server.ts` + `src/ai_trader/rpc.py` — how Start reaches Python.

### If the task is…

| Task | Look here first |
|---|---|
| Start button / session | `src/components/trading-home.tsx` → `src/routes/api/paper-session*.ts` → `src/lib/paper-engine.server.ts` → `src/ai_trader/rpc.py` → `src/ai_trader/session/runner.py` |
| Coinbase candles | `src/ai_trader/market_data/public.py` |
| Grok prompts / API | `src/ai_trader/ai/grok_client.py`, `payload.py`, `validate.py` |
| Paper P&L / fills | `src/ai_trader/paper/simulator.py`, `ledger.py`, `execution.py` |
| £100 account | `src/ai_trader/account/simulated.py` (`STARTING_CASH`) |
| Risk / size | `src/ai_trader/risk/engine.py`, `limits.py` |
| Kill switch | `src/ai_trader/kill_switch.py` |
| Logging trades / events | `src/ai_trader/db/repository.py` |
| Safety audit | `tests/test_safety.py`, `tests/test_safety_audit.py` |

### Hard rules for assistants

- Paper trading only. Never set `LIVE_TRADING_ALLOWED = True`.
- Never connect Alpaca live (`api.alpaca.markets`).
- Never bypass the risk engine.
- Never commit `.env`, API keys, tokens, or passwords.
- Do not rewrite working modules to “clean them up”.
- Do not change the home-screen UI unless the user asked.
- Run `python3 -m pytest` before committing.
- Keep the £100 starting cash unless the user asked to change it.

Full rules: [DEVELOPMENT.md](DEVELOPMENT.md).
