# Deployment

AI-Trader is a **persistent agent**, not a request handler. That single fact
decides the whole hosting shape, and it is why the app is split across two
hosts.

```
  Vercel                          Render (free)
  ┌──────────────────┐            ┌──────────────────────────────┐
  │ React UI         │            │ python -m ai_trader http     │
  │                  │  HTTPS     │  ├─ FastAPI transport        │
  │ /api/* routes ───┼───────────▶│  ├─ DeskWorker (owns session)│
  │                  │            │  ├─ risk / guardian / ledger │
  └──────────────────┘            │  └─ SQLite (ephemeral disk)  │
                                  └──────────────┬───────────────┘
                                                 │ checkpoint
                                                 ▼
                                  GitHub branch worker-endpoint
```

The browser talks only to Vercel. Vercel talks to Render. Closing the browser does not stop a running worker; a free Render service *will* sleep when idle. Sleep is reported honestly. On wake the worker restores the £100 ledger from the last GitHub checkpoint. It does not silently reset to a blank book, and it does not claim RUNNING while it is down.


## Why the frontend cannot host the engine

The Python engine holds state that must outlive an HTTP request:

- an open trading session with a poll loop on a background thread
- an in-memory simulator and ledger
- the survival state machine and its terminal latch
- a cursor into the candle series, so bars are never replayed

A serverless invocation is a fresh, short-lived sandbox with no `python3`
binary. Spawning Python from a Vercel function dies with the request. Faking
RUNNING in the browser is not a substitute — the browser is the control panel,
not the engine.

---

## 1. The worker (Render)

### Settings

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `PYTHONPATH=src python3 -m ai_trader http` |
| Health check path | `/health` |
| Instances | **1** — one writer, one ledger |
| Plan | **Free** |
| Disk | none — ledger is checkpointed to GitHub `worker-endpoint` |

`render.yaml` in the repository root carries the same values.

**Free plan behaviour, stated plainly:**

- The process sleeps after idle HTTP. The UI then shows the worker as asleep, not RUNNING.
- There is no persistent disk. On wake the worker restores SQLite + the TERMINAL latch from `worker-endpoint/snapshot.json`.
- A GitHub Actions job copies `GET /api/snapshot` every 10 minutes and on demand. Until the first checkpoint exists, a restart *would* start a fresh £100 book — the UI warns rather than hiding that.
- Pressing Start wakes the worker. That can take up to a minute.

### Environment variables

Set these on the worker service:

```
DATABASE_PATH=data/ai_trader.db
LOG_DIR=logs
TRADING_MODE=simulate
STARTING_EQUITY=100.00
BASE_CURRENCY=GBP
TERMINAL_THRESHOLD_PCT=0.40
KILL_SWITCH_ENGAGED=false
LOG_LEVEL=INFO
PERSIST_RESTORE=true
```

`AI_TRADER_API_TOKEN` is optional. If set on the worker, Start/Stop require it. If unset, this paper desk accepts Start from the Vercel UI. Live trading cannot be turned on with any of these.

Optional: `XAI_API_KEY` and `GROK_PAPER_ANALYSIS=true` for the analyst,
`BLS_API_KEY` to lift the anonymous quota on official data,
`GITHUB_TOKEN` if you want the worker to push checkpoints itself.

### The disk is not required on free

`data/` holds three things:

1. **The SQLite database** — every decision (including the HOLDs), order, fill, cost, calibration outcome and survival transition.
2. **The operator kill switch.**
3. **The TERMINAL latch.**

On Render free those files are ephemeral. The worker dumps them to GitHub after Start/Stop and on a 10-minute Actions job, and restores them on boot. A TERMINAL latch that made it into the checkpoint stays TERMINAL after a wake.

### Verify the worker

```bash
WORKER=https://<your-worker>.onrender.com

curl -s $WORKER/health
# {"ok":true,"live":false,"live_trading_allowed":false,"control_enabled":true}

curl -s $WORKER/api/status | jq '{status, running, balance, currency, engine}'
# {"status":"STOPPED","running":false,"balance":100,"currency":"GBP","engine":"python-worker"}
```

The worker answers `/health` with `control_enabled: true` so the UI can Start.
If `AI_TRADER_API_TOKEN` is set, Start/Stop require that header; if it is unset,
this paper desk accepts Start from the Vercel UI. Live trading is still impossible.

---

## 2. The frontend (Vercel)

Import the repository. The framework is detected automatically; no build
overrides are needed.

Set these environment variables (optional — the frontend defaults to the known Render URL):

```
PAPER_WORKER_URL=https://ai-trader-elxv.onrender.com
```

Server-side only. Never prefix `VITE_`. `AI_TRADER_API_TOKEN` is optional; if you set it, set the same value on Render.

### Who can press Start

With the setup above, the worker is protected — but anyone who can load your
Vercel URL can press Start. Choose one:

- **Vercel Deployment Protection** (Project → Settings → Deployment Protection).
  Password or SSO on the whole site. No code, no browser secret. Recommended.
- **`AI_TRADER_UI_TOKEN`** on Vercel. The frontend's own mutating routes then
  require an `x-ai-trader-token` header, which suits driving the API from a
  script but leaves the UI's buttons unable to authenticate.

The System page reports which of these is in effect rather than letting you
assume the deployment is private.

---

## 3. Start and stop paper trading

From the UI: the Start button on the home screen. It calls the frontend, which
calls the worker. The state it shows is the worker's real state — `STOPPED` →
`STARTING` (loading and validating a candle series) → `RUNNING` (bars loaded, a
price marked). It never shows RUNNING because a button was clicked.

From the command line:

```bash
curl -X POST $WORKER/api/start \
  -H "x-ai-trader-token: $AI_TRADER_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"symbol":"BTC-USD","source":"public","timeframe":"5m","bars":24}'

curl -X POST $WORKER/api/stop -H "x-ai-trader-token: $AI_TRADER_API_TOKEN"
```

Closing the browser does not stop the worker. Redeploying the worker does not
stop it either: the desired state is persisted, so a restart resumes a session
you never asked to stop. Stop is what stops it.

Duplicate Starts are safe — a Start against a live session returns that
session's status instead of replacing it.

---

## 4. Reading the deployed system

| Endpoint | What it tells you |
|---|---|
| `/health` | The process is up and whether control is configured |
| `/api/status` | Session state, equity, position, last price |
| `/api/system` | Per-component health; broken components report broken |
| `/api/performance` | P&L and calibration, computed from persisted data only |
| `/api/decisions` | The audit trail, including every HOLD and its reason |

Logs: Render dashboard → the service → Logs.

### Safe reset

There is no reset endpoint, by design. To start the experiment over, stop the
worker, delete `/var/data/ai_trader.db` from a Render shell, and redeploy. This
destroys the audit trail — that is what makes it a reset rather than an edit,
and it is why no HTTP route can do it.

A TERMINAL latch cannot be cleared from the UI, the API, or by the agent. That
is the point of it.

---

## 5. How live trading is disabled

Not by a flag you could flip:

- `ai_trader/safety.py` defines `LIVE_TRADING_ALLOWED = False` as a module
  constant. Startup asserts it.
- `TRADING_MODE` accepts `simulate` and `paper`. Anything containing `live`,
  `real`, `prod` or `cash` refuses to start the process.
- The Alpaca live API URL is blocked at the config layer; the paper adapter is
  observation only, and the session path traps `submit` on every broker.
- The HTTP worker has no order route, and no request field changes any of the
  above. Passing `{"live": true}` to Start does nothing.
- The analyst can only return an opinion. It cannot choose a ticker, a size, a
  price or a venue, and it cannot modify a risk limit.

Enabling real money would take deliberate code changes, a regulated venue
integration and a review — not an environment variable.

---

## 6. Local development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
npm install
npm run dev          # UI on :8080, Python worker spawned over stdio
```

With no `PAPER_WORKER_URL` set, the frontend spawns `python3 -m ai_trader rpc`
as a child and talks to it over stdin/stdout. That is the same engine and the
same command surface; only the transport differs.

To develop against the deployed worker instead:

```bash
PAPER_WORKER_URL=https://<your-worker>.onrender.com \
AI_TRADER_API_TOKEN=<token> npm run dev
```

A configured worker URL always wins over the local sidecar, so you cannot
accidentally end up with two desks and two ledgers.

To run the worker locally exactly as Render does:

```bash
PORT=8090 AI_TRADER_API_TOKEN=dev-token \
  PYTHONPATH=src python3 -m ai_trader http
```

---

## What is deliberately absent

- No live-trading path, and no environment variable that could create one.
- No real-money broker, and no bank-account integration.
- No CORS on the worker — the browser must never hold the control token, and
  absent CORS is what enforces that.
- No reset, edit or delete route for the ledger.
- No fabricated venue, order book, fill or P&L. If the worker cannot reach
  market data it reports STOPPED with the reason.
