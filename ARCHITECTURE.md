# AI-Trader architecture

Real implementation map. If this file disagrees with the code, the code wins.

Related: [PROJECT_STATUS.md](PROJECT_STATUS.md) · [README.md](README.md) · [DEVELOPMENT.md](DEVELOPMENT.md)

Live trading is **not** part of this system.

---

## Exact current data flow

```text
MARKET DATA          PublicCryptoFeed (Coinbase completed candles)
        ↓            or SimulatedMarketData (tests)
ANALYSIS             TechnicalAnalyst.analyse_series  (read-only)
        ↓
GROK / FIXTURE       GrokAnalyst.propose  or  FixtureAnalyst.propose
        ↓            BUY / SELL / HOLD only. Invalid → HOLD
DECISION LAYER       RepeatingGrokSource  (warmup + frequency; visible bars only)
        ↓
RISK ENGINE          RiskEngine.review_paper  (internal size / reject)
        ↓            Broker review() still rejects: allow_orders=False
PAPER EXECUTION      PaperSimulator + execution.py
        ↓            Signal on bar i close; fill at bar i+1 open ± 5bps/5bps
PAPER ACCOUNT        PaperLedger  (£100 start, GBP)
        ↓
TRADE / EVENT LOG    SQLite via Repository (continuous: persist on Stop)
        ↓
DASHBOARD            trading-home.tsx polls GET /api/paper-session
```

Grok cannot skip risk. The UI cannot skip risk. `SimulatedBroker.submit` is trapped during a paper session and raises if called.

Fail-closed market data (`_unavailable` in `session/runner.py`):

- `grok: STOPPED`
- `decision: HOLD`
- `trades: 0`
- `orders: []`, `fills: []`
- `balance` stays starting cash
- `data_error` set

---

## Two processes (preview)

```text
Browser  POST /api/paper-session/start|stop   GET /api/paper-session
   │
   ▼
TanStack Start (Node / Vite)     src/routes  src/components  src/lib
   └─ paper-engine.server.ts
         JSON lines on stdin/stdout
         ▼
python3 -m ai_trader rpc         src/ai_trader/rpc.py
   └─ Orchestrator.start_paper_session
         ▼
PaperSession                     src/ai_trader/session/runner.py
```

A FastAPI app (`src/ai_trader/dashboard/app.py`) exists for **pytest** and
`python3 -m ai_trader dashboard`. The live preview does **not** use a second
Python HTTP port.

---

## Frontend

| | |
|---|---|
| Stack | TanStack Start + React 19 + Vite |
| Home | `src/routes/index.tsx` → `src/components/trading-home.tsx` |
| Shows | Grok RUNNING/STOPPED, BUY/SELL/HOLD, £ balance, today’s P&L, position, Start, Stop |
| Performance | `src/routes/performance.tsx` |
| System | `src/routes/system.tsx` → `research-desk.tsx` (pipeline, modules, kill switch) |
| Start | POST `/api/paper-session/start` with BTC-USD, public, 24×5m, freq 8, warmup 8, continuous |
| Stop | POST `/api/paper-session/stop` |
| Poll | GET `/api/paper-session` every 2s **while** `running` |
| Does not | Fetch status on first paint (uses `paper-session-snapshot.json` until Start) |
| Orders | No |

Styles: `src/styles.css`. Dark desk. No live-trading control.

---

## Backend

Two backends, one engine:

1. **Preview / Start button** — Node API routes + stdio Python worker.
   - `src/lib/paper-engine.server.ts` spawns `python3 -u -m ai_trader rpc`
   - Auto-started by `paperEnginePlugin` in `vite.config.ts`
   - `startup.sh` starts `npm run dev` if the preview is down
2. **Python engine** — `src/ai_trader/`
   - `rpc.py` — health / status / start / stop
   - `pipeline/orchestrator.py` — only legal sequencer
   - `runtime.py` — settings, sqlite, kill switch, orchestrator
3. **FastAPI** — `dashboard/app.py` for tests/CLI only

Config: `src/ai_trader/config.py`, `.env.example`. Secrets never logged.

---

## Market data

| | |
|---|---|
| Public | `src/ai_trader/market_data/public.py` — `PublicCryptoFeed` |
| Simulated | `src/ai_trader/market_data/simulated.py`, `generator.py` |
| Validation | `validation.py`, `timeframes.py` |

Public feed:

- GET Coinbase Exchange candles
- Symbols: BTC-USD, ETH-USD
- Completed bars only (`close_at > now` skipped; future skipped)
- Stale if last close older than 3 bars
- Malformed / timeout / network / unknown symbol → `MarketDataUnavailableError` / `StaleMarketDataError`
- Refuses URLs containing `alpaca`
- Not an order path

---

## Analysis

`src/ai_trader/analysis/technical.py` + `indicators.py`

Read-only SMAs, slopes, returns, range, trend label. **Not** a trade signal. Never talks to a broker.

Called with the **visible** prefix of the series (`candles[0..i]`).

---

## Grok integration

| | |
|---|---|
| Real | `src/ai_trader/ai/grok_client.py` — `GrokAnalyst` |
| Fixture | `src/ai_trader/ai/fixture.py` — always HOLD, no network |
| Prompt / schema | `payload.py`, `validate.py` |

`GrokAnalyst.propose`:

- Off unless `GROK_PAPER_ANALYSIS` is true
- Missing key / timeout / network / invalid JSON → HOLD
- POST `https://api.x.ai/v1/chat/completions`, model `grok-4.6`
- Refuses any URL containing `alpaca`
- Must not send `tools` / `functions`

The Node worker enables `GROK_PAPER_ANALYSIS=true` when `XAI_API_KEY` exists. That is **paper analysis only**.

Grok has **no** execution path.

---

## Decision layer

`src/ai_trader/session/source.py` — `RepeatingGrokSource`

- Warmup bars: HOLD
- Then consult analyst every `grok_frequency` bars
- `_visible_only`: series length must equal `index+1` (no look-ahead)
- Maps BUY/SELL/HOLD to `PaperAction`
- Stop flag → HOLD

Orchestrator chooses Grok vs fixture **before** the session starts. The source never calls a broker.

---

## Risk engine

`src/ai_trader/risk/engine.py` + `limits.py`

- Constructed with `allow_orders=False`
- `review()` — broker path — **always rejects** in this build
- `review_paper()` — internal paper size/reject only
- Limits: £100 start, 2%/£2, 2 positions, 5% daily loss, 10 trades/day, no leverage, 2% stop, 2R TP, long-only, no shorts
- AI cannot change limits (frozen dataclass)

Grok output is an input to risk, never a substitute for it.

---

## Paper account

`src/ai_trader/account/simulated.py` — snapshot object, £100, no fills of its own.

Actual cash/positions: `src/ai_trader/paper/ledger.py` (`PaperLedger`), started at the same £100.

Not convertible to live. No withdrawals. No leverage.

---

## Paper execution

`src/ai_trader/paper/simulator.py` + `execution.py`

- Sequential walk, no look-ahead (`seen_future` must stay False)
- Decision on bar **i close** using candles `[0..i]`
- BUY pending → fill at bar **i+1 open** + half spread + slip
- SELL/close similarly, adverse
- Ambiguous SL/TP candle: **stop first**
- Last bar: pending may remain unfilled
- `flatten_at_end=False` in continuous mode
- Stop cancels pending and blocks new entries
- **No broker, no network**

---

## Event logging

`src/ai_trader/db/repository.py` + `schema.sql`

SQLite (`data/ai_trader.db`, gitignored): `ai_decisions`, `trades`, `positions`, `account_snapshots`, `events`, paper-run blobs.

- Non-continuous session: persist on start completion
- Continuous session: persist on **Stop**
- Nothing auto-trades from the database

---

## API routes

**TanStack (preview)**

| Method | Path | File |
|---|---|---|
| GET | `/api/paper-session` | `src/routes/api/paper-session.ts` |
| POST | `/api/paper-session/start` | `src/routes/api/paper-session.start.ts` |
| POST | `/api/paper-session/stop` | `src/routes/api/paper-session.stop.ts` |

All three call `paperEngineCommand` in `src/lib/paper-engine.server.ts`.

**FastAPI (tests/CLI)** — same path names on `dashboard/app.py`. `/api/orders` records a blocked event and does not place an order.

---

## Session lifecycle

`src/ai_trader/session/runner.py` + `config.py`

1. **Start (continuous):** increment generation, spawn daemon thread, return `status()` immediately with `grok: RUNNING`.
2. Thread `_walk`s historical completed bars (`finalize=False`).
3. Loop: wait `poll_seconds` (15s), fetch feed, keep candles with timestamp **> last**, `sim.extend` from cursor. No replay.
4. Bad data → `_unavailable`, thread exits, Grok STOPPED, HOLD, zero trades.
5. **Stop:** `stopped=True`, event set, pending cancelled, new trades blocked, positions left as they are. Orchestrator persists if continuous.

Stale Start threads are ignored via `_generation`.

---

## Safety / kill-switch system

| Layer | File | Behaviour |
|---|---|---|
| Live flag | `safety.py` | `LIVE_TRADING_ALLOWED = False`. No env override. |
| Modes | `safety.py` | Only `simulate` and `paper`. `live`/`prod`/`real`/`cash` refuse to start. |
| Alpaca URL | `safety.py` | `api.alpaca.markets` blocked. Paper host only. |
| Kill switch | `kill_switch.py` | File `data/KILL_SWITCH`. Default **engaged**. Disengage ≠ live. |
| Where halt applies | orchestrator `dry_run`, `grok_paper_cycle`, `benchmark` call `assert_clear()` | |
| Where halt does **not** apply today | `start_paper_session` / `PaperSession._walk` pass `kill_switch=False` into the simulator | |
| Risk | `allow_orders=False`; `review()` rejects broker orders | |
| Brokers | `simulated.py` submit raises; `alpaca_paper.py` live host blocked | |
| Grok | no tools, no alpaca URL | |
| Tests | `tests/test_safety.py`, `test_safety_audit.py` | |

Do not remove these to “make Start work”.

---

## Module map (callers / orders)

### Safety and process

| Path | Does | Called by | Calls | Orders | Kind |
|---|---|---|---|---|---|
| `src/ai_trader/safety.py` | Live flag, modes, live-URL block | config, orchestrator, brokers, session, tests | nothing external | No | Invariant |
| `src/ai_trader/kill_switch.py` | File-backed halt | runtime, orchestrator dry-run paths, dashboard | filesystem | No | Local |
| `src/ai_trader/config.py` | Env settings, no secret logs | runtime, grok, alpaca | `assert_safe_to_run` | No | Local |
| `src/ai_trader/runtime.py` | Wires process | rpc, FastAPI, CLI | config, db, kill switch, orchestrator | No | Bootstrap |
| `src/ai_trader/rpc.py` | Stdio JSON: health/status/start/stop | Node worker | orchestrator | No | Paper IPC |
| `src/ai_trader/pipeline/orchestrator.py` | Only legal sequencer; traps broker.submit on session | rpc, FastAPI, CLI | feeds, analysis, Grok/fixture, risk, PaperSession, db | Must not | Paper |

### Market, analysis, Grok

| Path | Does | Called by | Calls | Orders | Kind |
|---|---|---|---|---|---|
| `market_data/public.py` | Coinbase completed candles | session when source=public | Coinbase GET | No | External read-only |
| `market_data/simulated.py` | Deterministic OHLCV | tests, benchmark | none | No | Paper |
| `analysis/technical.py` | Indicators | session, simulator | series | No | Read-only |
| `ai/grok_client.py` | Paper analysis | orchestrator if flag on | xAI chat | No | External analysis |
| `ai/fixture.py` | Always HOLD | default analyst | none | No | Paper |
| `session/source.py` | Repeated decisions, no look-ahead | PaperSession | analyst.propose | No | Paper |

### Risk, account, execution

| Path | Does | Called by | Calls | Orders | Kind |
|---|---|---|---|---|---|
| `risk/engine.py` | Gate + paper size | simulator, session | limits | Internal paper only | Paper |
| `account/simulated.py` | £100 snapshot | orchestrator, ledger default | none | No | Paper |
| `paper/simulator.py` | Sequential fills | PaperSession | risk, ledger, execution | Internal only | Paper |
| `paper/ledger.py` | Cash, positions, halt | simulator | none | Ledger only | Paper |
| `paper/execution.py` | Fill/SL/TP math | simulator | none | Pricing | Paper |
| `session/runner.py` | Start/stop/continuous | orchestrator | feed, source, simulator | Via simulator | Paper |

### Brokers (not the Start fill path)

| Path | Does | Orders | Kind |
|---|---|---|---|
| `broker/simulated.py` | Stub; submit raises | No | Stub |
| `broker/alpaca_paper.py` | Paper host only; live URL blocked | Gated; Start = NOT USED without keys | Optional paper adapter |

### Web + persistence

| Path | Does | Orders | Kind |
|---|---|---|---|
| `components/trading-home.tsx` | Start/Stop UI | No | UI |
| `routes/api/paper-session*.ts` | HTTP → stdio worker | No | UI server |
| `lib/paper-engine.server.ts` | Spawn/keep rpc worker | No | Local IPC |
| `db/repository.py` | SQLite events/runs | No | Local |
| `dashboard/app.py` | FastAPI for tests | `/api/orders` blocked | Paper HTTP |
| `vite.config.ts`, `startup.sh` | Preview + auto worker | No | Deploy/preview |
| `tests/` | Assert no live path | Assert none placed | Local |

---

## What never happens on Start

- No `https://api.alpaca.markets`
- No real-money order
- No look-ahead
- No `LIVE_TRADING_ALLOWED = True`
- `broker: NOT USED` when Alpaca paper is not configured (normal)
