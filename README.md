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

This repository is **public**. Still never put API keys in it.

---

## What the application currently does

- Paper-only research desk: £100 simulated GBP account.
- Home screen Start runs a **continuous** session: public Coinbase **BTC-USD** 5-minute **completed** candles → analysis → Grok or fixture → BUY/SELL/HOLD → risk → internal paper fills.
- Stop blocks new paper trades.
- Fail-closed market data: bad/stale/timeout → HOLD, Grok STOPPED, zero trades.
- Fixture Grok always HOLD. Real Grok is paper analysis only (opt-in / key present).
- Risk engine sizes or rejects **internal** paper orders. Broker orders stay disabled.
- SQLite logs events and paper runs (continuous persist on Stop).

Current snapshot: [PROJECT_STATUS.md](PROJECT_STATUS.md).  
Call graph: [ARCHITECTURE.md](ARCHITECTURE.md).

## What it does NOT do

- **Live trading.** `LIVE_TRADING_ALLOWED` is hardcoded `False`. There is no live mode.
- **Real money.** No cash broker, no withdrawals, no live Alpaca.
- **Alpaca live host.** `https://api.alpaca.markets` is blocked.
- **Default Alpaca paper fills.** Start reports `broker: NOT USED` unless paper keys + paper mode are set (they are not, in the normal desk).
- **Look-ahead.** Grok and the simulator see candles `[0..i]` only. The forming Coinbase bar is dropped.
- **Guaranteed profit.** No strategy in this repo has a demonstrated out-of-sample edge. Do not claim one.
- **Unrestricted Grok.** Grok cannot skip risk, cannot send tools, cannot call a broker.
- **Order buttons.** Dashboard `/api/orders` is blocked. Start is paper session, not “place order”.
- **Hydrate-on-refresh.** The home screen does not GET status on first paint; it shows a snapshot until Start.

It is **not** a live trading platform.

---

## Exact data flow

```text
MARKET DATA → ANALYSIS → GROK → BUY/SELL/HOLD → RISK ENGINE
        → PAPER EXECUTION → PAPER ACCOUNT → TRADE/EVENT LOG
```

On Start (home button):

1. Coinbase completed 5m BTC-USD candles (`market_data/public.py`)
2. `TechnicalAnalyst` on the visible prefix only
3. `GrokAnalyst` or `FixtureAnalyst` → BUY / SELL / HOLD
4. `RepeatingGrokSource` (warmup 8, then every 8 bars)
5. `RiskEngine.review_paper` — internal size or reject
6. `PaperSimulator` / `PaperLedger` — next-bar open fill, £100 book
7. Status JSON to the UI; SQLite persist on Stop (continuous)

If market data is unsafe: HOLD, Grok STOPPED, zero trades, no execution.

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
- A proven profitable strategy

---

## AI ASSISTANT DEVELOPMENT GUIDE

This section is for ChatGPT, Claude, Grok, or any other assistant cloning
[charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader).

**Repository:** `charliebrock2004/ai-trader`  
**Branch:** `main`  
**Package version:** `0.1.0`  
**Documented commit:** tip of `main` — see [PROJECT_STATUS.md](PROJECT_STATUS.md).

### Read first (in this order)

1. [PROJECT_STATUS.md](PROJECT_STATUS.md) — what actually works today.
2. [DEVELOPMENT.md](DEVELOPMENT.md) — rules. Do not skip.
3. [ARCHITECTURE.md](ARCHITECTURE.md) — real module map and data flow.
4. `src/ai_trader/safety.py` — live trading must stay False.
5. `src/ai_trader/pipeline/orchestrator.py` — the only legal pipeline wiring.
6. `src/components/trading-home.tsx` — Start / Stop UI (do not redesign unless asked).
7. `src/lib/paper-engine.server.ts` + `src/ai_trader/rpc.py` — how Start reaches Python.

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
