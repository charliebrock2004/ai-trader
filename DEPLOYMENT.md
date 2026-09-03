# Deployment

AI-Trader is a **persistent agent**, not a request handler. That single fact
decides the whole hosting shape.

## Why serverless does not work here

The Python paper engine holds state that must outlive an HTTP request:

- an open trading session with a poll loop on a background thread
- an in-memory simulator and ledger
- the survival state machine and its terminal latch
- a cursor into the candle series so bars are never replayed

A serverless invocation is a fresh, short-lived sandbox with no `python3`
binary and no shared process. Spawning Python from a Vercel function dies with
the request. Faking RUNNING in the browser is not an acceptable substitute —
the browser is the control panel, not the engine.

## What actually runs today

**Operational desk: Grok Build preview**

```
npm run dev
  Vite :8080  →  spawn python3 -m ai_trader rpc
                  JSON lines over stdin/stdout
                  owns session, ledger, survival
```

Start/Stop in the UI talk to this worker. Closing the browser does not stop
it. Stopping the preview process does.

**Grok Build production (Nitro vercel / nodejs22.x)**

No Python. Start fails closed with a clear reason. The UI still loads and
shows £100 STOPPED. It will not claim RUNNING.

**Correct production shape (not wired to grok.me)**

One long-lived Node (or Python dashboard) process owns the worker as a child,
with `data/` on a volume:

```
  node server :8080
    └─ python3 -m ai_trader rpc
  volume: sqlite + terminal latch
```

There is no published `ai-trader.grok.me` worker host. Do not pretend there is.

## Running it

Local / preview:

```bash
npm run dev            # UI on :8080, worker spawned automatically
```

Python dependencies:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## The volume is not optional

`data/` holds the SQLite database (decisions, orders, fills, outcomes, costs,
survival transitions) and the terminal-latch file. Mount it on persistent
storage. If the container is replaced and `data/` was ephemeral:

- the agent's entire decision history is gone, and
- **a TERMINATED agent would come back alive**, which is the one failure this
  system must not have.

The latch is written to disk *and* to the database, and startup refuses to run
if either says TERMINAL — but both live in `data/`.

## Environment

Set on the host, never in the repository:

| Variable | Purpose |
|---|---|
| `AI_TRADER_API_TOKEN` | Required to call mutating endpoints in production. Preview allows Start without it. |
| `XAI_API_KEY` | Grok analysis. Absent → fixture analyst, which always HOLDs. |
| `GROK_PAPER_ANALYSIS` | `true` to enable Grok paper analysis. Cannot enable live trading. |
| `DATABASE_PATH` | SQLite location. Point it at the mounted volume. |
| `TRADING_MODE` | `simulate` or `paper`. Any live-ish token refuses to start. |

Never prefix a secret with `VITE_` — those are inlined into the browser bundle.

## What is deliberately absent

- No live-trading path, and no environment variable that could create one.
- No real-money broker. The Alpaca paper adapter is dormant and observation
  only; the session path traps `submit` on every broker.
- No second HTTP port for Python. Stdio only.
- No fabricated venue, order book, or fill.
