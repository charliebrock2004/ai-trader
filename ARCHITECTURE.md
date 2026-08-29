# AI-Trader architecture

Simple map of the paper-only desk. Live trading is not part of this system.

Related: [README.md](README.md), [DEVELOPMENT.md](DEVELOPMENT.md).

---

## Two processes

```text
Browser
  └─ POST /api/paper-session/start|stop
  └─ GET  /api/paper-session
        │
        ▼
TanStack Start (Node / Vite)          src/routes, src/components, src/lib
  └─ paper-engine.server.ts
        │  JSON lines on stdin/stdout
        ▼
python3 -m ai_trader rpc              src/ai_trader/rpc.py
  └─ Orchestrator.start_paper_session
        ▼
PaperSession walk                     src/ai_trader/session/runner.py
```

A FastAPI app (`src/ai_trader/dashboard/app.py`) exists for pytest and
`python3 -m ai_trader dashboard`. The live preview uses the Node app, not a
second Python HTTP port.

---

## End-to-end Start path

```text
trading-home.tsx
        POST /api/paper-session/start
routes/api/paper-session.start.ts
        paperEngineCommand("start")
lib/paper-engine.server.ts
        spawn python3 -m ai_trader rpc   (once)
        JSON { cmd: "start", payload }
ai_trader/rpc.py handle()
orchestrator.start_paper_session(symbol=BTC-USD, source=public, continuous=True)
        assert_safe_to_run
        trap SimulatedBroker.submit
        analyst = GrokAnalyst if GROK_PAPER_ANALYSIS else FixtureAnalyst
PaperSession.start()
        continuous → background thread, return status immediately (grok RUNNING)
        _walk completed candles only (no look-ahead)
PublicCryptoFeed → TechnicalAnalyst → RepeatingGrokSource → RiskEngine.review_paper
        → PaperSimulator / PaperLedger  (internal fills)
status JSON → UI  (balance, today_pnl, grok, decision, position)
```

---

## Module map

For each module: path, role, callers, callees, whether it can place orders,
paper-only vs external/read-only.

### Safety and process

| | |
|---|---|
| **Path** | `src/ai_trader/safety.py` |
| **Does** | Hard invariant `LIVE_TRADING_ALLOWED = False`. Allowed modes `simulate`/`paper`. Blocks Alpaca live URL. |
| **Called by** | config, orchestrator, brokers, session runner, tests |
| **Calls** | nothing external |
| **Orders** | No |
| **Kind** | Paper-only invariant |

| | |
|---|---|
| **Path** | `src/ai_trader/kill_switch.py` |
| **Does** | File-backed halt (`data/KILL_SWITCH`). Default engaged. Disengage ≠ live trading. |
| **Called by** | runtime, orchestrator, dashboard/system |
| **Calls** | filesystem only |
| **Orders** | No. When engaged, pipeline must refuse to run. |
| **Kind** | Local |

| | |
|---|---|
| **Path** | `src/ai_trader/config.py` |
| **Does** | Env-backed settings. Never logs secrets. Validates mode + Alpaca URL at load. |
| **Called by** | runtime, grok client, alpaca paper, CLI |
| **Calls** | `safety.assert_safe_to_run` |
| **Orders** | No |
| **Kind** | Local config |

| | |
|---|---|
| **Path** | `src/ai_trader/runtime.py` |
| **Does** | Wires settings, logging, SQLite, kill switch, orchestrator. |
| **Called by** | rpc, FastAPI dashboard, CLI |
| **Calls** | config, repository, kill switch, orchestrator |
| **Orders** | No |
| **Kind** | Process bootstrap |

| | |
|---|---|
| **Path** | `src/ai_trader/rpc.py` |
| **Does** | Stdio JSON-line worker: `health`, `status`, `start`, `stop`. |
| **Called by** | Node `paper-engine.server.ts` via `python3 -m ai_trader rpc` |
| **Calls** | `get_runtime().orchestrator` |
| **Orders** | No |
| **Kind** | Paper-only IPC |

| | |
|---|---|
| **Path** | `src/ai_trader/__main__.py` |
| **Does** | CLI: `dashboard`, `status`, `init-db`, `grok-paper`, `benchmark`, `paper-session`, `rpc`. |
| **Called by** | `python3 -m ai_trader` |
| **Calls** | uvicorn (dashboard only), rpc.serve, orchestrator |
| **Orders** | No |
| **Kind** | CLI |

---

### Pipeline

| | |
|---|---|
| **Path** | `src/ai_trader/pipeline/orchestrator.py` |
| **Does** | Only legal sequencer: market data → analysis → AI → risk → paper session. Traps broker.submit on the paper-session path. |
| **Called by** | rpc, FastAPI routes, CLI |
| **Calls** | PublicCryptoFeed, SimulatedMarketData, TechnicalAnalyst, GrokAnalyst / FixtureAnalyst, RiskEngine, PaperSession, Repository, AlpacaPaperBroker.account (attach metadata only when configured) |
| **Orders** | Must not. Raises if SimulatedBroker.submit is touched during a paper session. |
| **Kind** | Paper-only orchestration |

---

### Market data

| | |
|---|---|
| **Path** | `src/ai_trader/market_data/public.py` |
| **Does** | Coinbase Exchange GET candles. BTC-USD / ETH-USD. Completed bars only. Stale-bar fail-closed. |
| **Called by** | orchestrator (source=`public`), PaperSession._load_series |
| **Calls** | `https://api.exchange.coinbase.com/products/{product}/candles` (read-only) |
| **Orders** | No |
| **Kind** | External **read-only** |

| | |
|---|---|
| **Path** | `src/ai_trader/market_data/simulated.py`, `generator.py`, `scenarios.py` |
| **Does** | Deterministic offline OHLCV (SIM-UP / SIM-DOWN / etc.). |
| **Called by** | orchestrator, tests, benchmark |
| **Calls** | nothing external |
| **Orders** | No |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/market_data/validation.py`, `timeframes.py`, `base.py` |
| **Does** | Candle validation, timeframe seconds, provider interface. |
| **Called by** | public + simulated feeds, session |
| **Calls** | nothing |
| **Orders** | No |
| **Kind** | Local |

---

### Analysis and Grok

| | |
|---|---|
| **Path** | `src/ai_trader/analysis/technical.py`, `indicators.py` |
| **Does** | SMAs, slopes, returns, range, trend label. Not a trade signal. |
| **Called by** | orchestrator, PaperSimulator, RepeatingGrokSource path |
| **Calls** | candle series only |
| **Orders** | No |
| **Kind** | Paper-only, read-only |

| | |
|---|---|
| **Path** | `src/ai_trader/ai/grok_client.py` |
| **Does** | `GrokAnalyst.propose` → BUY/SELL/HOLD. Model `grok-4.6`. Fail-safe HOLD. Refuses any `alpaca` URL. No tools. |
| **Called by** | orchestrator when `GROK_PAPER_ANALYSIS` is on |
| **Calls** | `https://api.x.ai/v1/chat/completions` |
| **Orders** | No |
| **Kind** | External **analysis only** (opt-in) |

| | |
|---|---|
| **Path** | `src/ai_trader/ai/fixture.py` |
| **Does** | Default analyst. HOLD (or scenario fixture). No network. |
| **Called by** | orchestrator when Grok paper analysis is off; tests |
| **Calls** | nothing |
| **Orders** | No |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/ai/payload.py`, `validate.py`, `base.py` |
| **Does** | Prompt, JSON schema, parse/validate Grok output. Invalid → HOLD. |
| **Called by** | GrokAnalyst, RepeatingGrokSource (via propose) |
| **Calls** | nothing |
| **Orders** | No |
| **Kind** | Local |

---

### Risk, paper account, execution

| | |
|---|---|
| **Path** | `src/ai_trader/risk/engine.py`, `limits.py` |
| **Does** | Hard gate. `allow_orders=False`. `review_paper()` may size **internal** paper orders only. AI cannot change limits. |
| **Called by** | PaperSimulator, PaperSession, orchestrator |
| **Calls** | RiskLimits |
| **Orders** | Never a broker order. Internal paper size only. |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/account/simulated.py` |
| **Does** | Offline £100.00 GBP snapshot object. No fills of its own. |
| **Called by** | orchestrator, PaperLedger default cash |
| **Calls** | nothing |
| **Orders** | No |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/paper/simulator.py` |
| **Does** | Sequential walk. Signal on bar i close using candles `[0..i]`. Fill at next open ± 5 bps spread + 5 bps slip. STOP cancels pending. |
| **Called by** | PaperSession._walk |
| **Calls** | analyse_series, RiskEngine.review_paper, PaperLedger, execution helpers |
| **Orders** | Internal paper fills only. No broker. No network. |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/paper/ledger.py` |
| **Does** | Cash, positions, fills, daily loss halt. Starts at £100. |
| **Called by** | PaperSimulator |
| **Calls** | nothing |
| **Orders** | Internal ledger only |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/paper/execution.py` |
| **Does** | Fill-price math and intrabar SL/TP. Ambiguous candle → stop first. |
| **Called by** | PaperSimulator |
| **Calls** | nothing |
| **Orders** | Pricing only |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/paper/signals.py`, `models.py`, `performance.py` |
| **Does** | Signal sources, order/fill dataclasses, performance summary. |
| **Called by** | simulator, session, benchmark |
| **Calls** | nothing |
| **Orders** | No |
| **Kind** | Paper-only |

---

### Session (continuous paper)

| | |
|---|---|
| **Path** | `src/ai_trader/session/runner.py` |
| **Does** | Start/stop. Continuous mode: background thread, poll for **new completed** timestamps, no look-ahead, no flatten until asked. |
| **Called by** | orchestrator |
| **Calls** | PublicCryptoFeed or generator, RepeatingGrokSource, PaperSimulator |
| **Orders** | Delegates to paper simulator only |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/session/source.py` |
| **Does** | Consult Grok every N bars after warmup. Other bars HOLD. Visible candles only. |
| **Called by** | PaperSession |
| **Calls** | Analyst.propose, analyse_series |
| **Orders** | No |
| **Kind** | Paper-only |

| | |
|---|---|
| **Path** | `src/ai_trader/session/config.py` |
| **Does** | Frozen session settings. Sources: `simulated` \| `public`. Starting balance £100. |
| **Called by** | orchestrator, PaperSession |
| **Calls** | timeframe helpers |
| **Orders** | No |
| **Kind** | Paper-only |

---

### Brokers (not the Start path)

| | |
|---|---|
| **Path** | `src/ai_trader/broker/simulated.py` |
| **Does** | In-process stub. `submit` always raises `OrderPlacementDisabledError`. |
| **Called by** | orchestrator (trapped during paper session) |
| **Calls** | nothing |
| **Orders** | Explicitly no |
| **Kind** | Paper-only stub |

| | |
|---|---|
| **Path** | `src/ai_trader/broker/alpaca_paper.py` |
| **Does** | Alpaca **paper** host only (`paper-api.alpaca.markets`). Live URL blocked. Not used by Start unless paper keys + mode=paper. |
| **Called by** | orchestrator._attach_alpaca_paper |
| **Calls** | Alpaca paper REST **only if configured** |
| **Orders** | `submit` still gated; current Start reports `broker: NOT USED` without keys |
| **Kind** | External paper adapter (optional, not live) |

---

### Persistence and Python dashboard

| | |
|---|---|
| **Path** | `src/ai_trader/db/repository.py`, `schema.sql` |
| **Does** | SQLite: events, decisions, paper runs. No auto-trading. |
| **Called by** | runtime, orchestrator persist |
| **Calls** | local sqlite file (`data/ai_trader.db`, gitignored) |
| **Orders** | No |
| **Kind** | Local |

| | |
|---|---|
| **Path** | `src/ai_trader/dashboard/app.py` |
| **Does** | FastAPI status UI + `/api/paper-session/*` for tests. Order routes return blocked. |
| **Called by** | `python3 -m ai_trader dashboard`, pytest TestClient |
| **Calls** | orchestrator |
| **Orders** | No (`/api/orders` records a blocked event) |
| **Kind** | Paper-only HTTP (tests/CLI) |

---

### Web desk (preview)

| | |
|---|---|
| **Path** | `src/components/trading-home.tsx` |
| **Does** | Home: Grok status, decision, Start/Stop, £ balance, P&L, position. |
| **Called by** | `src/routes/index.tsx` |
| **Calls** | `fetch("/api/paper-session...")` |
| **Orders** | No |
| **Kind** | UI |

| | |
|---|---|
| **Path** | `src/routes/api/paper-session.ts`, `paper-session.start.ts`, `paper-session.stop.ts` |
| **Does** | TanStack server handlers. Defaults BTC-USD public 5m continuous. |
| **Called by** | browser |
| **Calls** | `paperEngineCommand` |
| **Orders** | No |
| **Kind** | UI server |

| | |
|---|---|
| **Path** | `src/lib/paper-engine.server.ts` |
| **Does** | Spawns/keeps the Python rpc worker. No extra TCP port. |
| **Called by** | API routes, Vite `paperEnginePlugin` |
| **Calls** | child_process `python3 -u -m ai_trader rpc` |
| **Orders** | No |
| **Kind** | Local IPC |

| | |
|---|---|
| **Path** | `src/routes/performance.tsx`, `system.tsx`, `paper.tsx` |
| **Does** | Performance page; System (pipeline, kill switch, modules); extra paper view. |
| **Called by** | router links |
| **Calls** | snapshots / status APIs |
| **Orders** | No |
| **Kind** | UI |

| | |
|---|---|
| **Path** | `vite.config.ts`, `startup.sh`, `package.json` |
| **Does** | Dev server on `0.0.0.0:8080`, auto-start paper worker, Vercel/Nitro build preset. |
| **Called by** | `npm run dev` / `startup.sh` |
| **Calls** | Vite, paper worker |
| **Orders** | No |
| **Kind** | Deployment / preview |

---

### Tests and benchmark

| | |
|---|---|
| **Path** | `tests/` |
| **Does** | Paper session, public feed, Grok paper, risk, safety audit, rpc, dashboard. |
| **Called by** | `python3 -m pytest` |
| **Calls** | engine via TestClient / direct Python |
| **Orders** | Assert they are **not** placed |
| **Kind** | Local |

| | |
|---|---|
| **Path** | `src/ai_trader/benchmark/` |
| **Does** | Walk-forward comparison: buy-and-hold, SMA, random, Grok on the **same paper book**. |
| **Called by** | orchestrator.benchmark, CLI |
| **Calls** | PaperSimulator, fixture/Grok |
| **Orders** | Paper only |
| **Kind** | Paper-only |

---

## What never happens on Start

- No call to `https://api.alpaca.markets`
- No Alpaca order when keys are absent (`broker: NOT USED`)
- No look-ahead (Grok sees candles `[0..i]` only)
- No live trading flag flip
- No real money
