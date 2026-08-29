# AI-Trader — project status

**Source of truth for ChatGPT and other assistants.**  
Read this first, then [ARCHITECTURE.md](ARCHITECTURE.md) and [DEVELOPMENT.md](DEVELOPMENT.md).

| | |
|---|---|
| Repository | [charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader) (public) |
| Branch | `main` |
| Package version | `0.1.0` |
| Product | Paper-trading research desk. **Not** a live broker. |
| Documented commit | `054d01edfaa6d9b9b426f32600b862e1a92c0bbb` on `main` |

If this file disagrees with the code, **the code wins**. Update this file after every significant change.

---

## Current version / status

**Working paper desk.** Start runs a continuous £100 paper session on public Coinbase BTC-USD 5-minute **completed** candles. Grok (or the fixture) proposes BUY / SELL / HOLD. The risk engine sizes or rejects **internal** paper fills. Stop blocks new paper trades. Live trading is disabled in code.

This is a research prototype. It has **not** demonstrated a profitable trading edge.

---

## What works

- Home screen: balance, today’s P&L, Grok RUNNING/STOPPED, current decision, position, Start, Stop, Performance, System.
- Start → `POST /api/paper-session/start` → Python stdio worker → sequential paper walk.
- Stop → `POST /api/paper-session/stop` → pending cancelled, new paper trades blocked.
- Public Coinbase feed: BTC-USD / ETH-USD, completed bars only, forming candle dropped, stale/malformed/timeout/network **fail closed** (HOLD, Grok STOPPED, zero trades).
- Fixture Grok always HOLD. Real Grok paper analysis when `GROK_PAPER_ANALYSIS=true` **and** `XAI_API_KEY` is set; invalid/timeout/network → HOLD.
- Internal paper ledger starts at **£100 GBP**. Spread 5 bps + slip 5 bps. Next-bar open fills. No look-ahead.
- Risk limits: 2%/£2, max 2 positions, 5% daily loss, 10 trades/day, no leverage, 2% stop / 2R TP, long-only.
- Kill switch file exists (`data/KILL_SWITCH`), default engaged. It blocks dry-run, grok-paper cycle, and benchmark. (See known issues for Start.)
- `LIVE_TRADING_ALLOWED = False`. Alpaca live host blocked. `allow_orders=False`. SimulatedBroker.submit raises. Grok refuses `alpaca` URLs.
- SQLite event/decision/paper-run logging. Continuous sessions persist on **Stop**.
- Automated pytest suite (see Latest tests).

---

## What is being tested

pytest under `tests/` covers:

- Safety invariant and live-host audit
- Public Coinbase parse + fail-closed (stale, malformed, timeout, network)
- Paper session sequential walk, no look-ahead, Stop
- Risk sizing and broker-order rejection
- Fixture + Grok paper HOLD fallbacks
- RPC start/stop never uses a broker
- FastAPI dashboard order routes blocked

---

## What is unfinished

- No out-of-sample proof that Grok (or any strategy) makes money. Do not claim an edge.
- Alpaca **paper** adapter exists but Start reports `broker: NOT USED` when keys are absent (the normal state). Not wired as the default fill path.
- Home screen does **not** GET `/api/paper-session` on first paint. It shows a snapshot until Start (then polls while running).
- File-backed kill switch does **not** currently block the home-screen Start paper session (Start passes `kill_switch=False` into the simulator so the desk can run while the halt file stays engaged). Dry-run / benchmark **are** blocked.
- Continuous session is not persisted on every new bar — persist on Stop.
- Dual UI: TanStack Start is the preview; FastAPI templates exist for pytest/CLI.

---

## Current market-data provider

**Coinbase Exchange public REST**, read-only.

- Implementation: `src/ai_trader/market_data/public.py` (`PublicCryptoFeed`)
- URL: `GET https://api.exchange.coinbase.com/products/{BTC-USD|ETH-USD}/candles`
- Not a broker. Refuses any URL containing `alpaca`.

Simulated OHLCV (`SIM-UP`, etc.) still exists for tests and benchmarks.

---

## Current symbols

Public Start path: **BTC-USD** (ETH-USD is allowed by the feed).

Fixture/tests also use simulated symbols (`SIM-UP`, `SIM-DOWN`, …).

---

## Current timeframe

**5m** on Start. Public feed also allows `1m`, `15m`, `1h`, `1d`. **4h is not supported** by Coinbase granularity in this adapter.

Completed candles only. Forming bar excluded (`close_at > now` dropped). Stale if last completed close is older than **3 bars**.

---

## Current Grok status

| | |
|---|---|
| Default in Python | Fixture HOLD (`GROK_PAPER_ANALYSIS` defaults false) |
| Preview worker | Node sets `GROK_PAPER_ANALYSIS=true` when `XAI_API_KEY` is present |
| Model | `grok-4.6` |
| Endpoint | `POST https://api.x.ai/v1/chat/completions` |
| Output | JSON BUY / SELL / HOLD only. No tools. No broker. |
| Cap | warmup 8 bars, then every 8 completed 5m bars (~1 call / 40 minutes after warmup) |
| Fail-safe | missing key, timeout, bad JSON, network → **HOLD** |
| Edge | **Not demonstrated.** Sitting in cash is not an edge. |

---

## Current paper-account status

- Starting cash **£100.00 GBP** (`STARTING_CASH` in `src/ai_trader/account/simulated.py`)
- Fills go to `PaperLedger` / `PaperSimulator`, not a broker
- Currency GBP on the simulated book (Alpaca paper would be USD **if** configured; it is not used on Start)

---

## Current risk / safety status

- Risk engine is a hard gate for paper size (`review_paper`). Broker `review()` still rejects all orders because `allow_orders=False`.
- Kill switch file exists; default engaged.
- `LIVE_TRADING_ALLOWED = False` with no env override.
- Live Alpaca URL blocked.

---

## Current Alpaca status

- Adapter: `src/ai_trader/broker/alpaca_paper.py`
- Host allowed: `paper-api.alpaca.markets` only
- Host blocked: `api.alpaca.markets`
- Start path: **NOT USED** unless paper keys + `TRADING_MODE=paper` (not the default)

---

## Current live-trading status

**Disabled.** There is no supported live mode. Do not enable it.

---

## Before real-money trading (security milestone)

Real-money trading is **not enabled** and is **not the next step**. Treat it as a
separate security milestone, not a config flip.

Before any real-money use:

- All brokerage credentials must remain **outside GitHub**.
- API keys must be stored as environment / secret variables on the machine or
  host — never in source, never in `.env.example`, never in docs.
- Credentials must **never** appear in source code.
- Credentials must **never** appear in logs.
- Credentials must **never** appear in the UI.
- If a credential was ever exposed in git history, it **must be revoked and
  replaced** before it is used. Deleting the file is not enough.
- `LIVE_TRADING_ALLOWED` must stay `False` until that milestone is explicitly
  designed, tested, and reviewed. There is no env override today.
- Alpaca live (`https://api.alpaca.markets`) must stay blocked.
- Grok must still be unable to bypass the risk engine or place broker orders.

This repository is public source + documentation only. It is not a wallet and
not a broker.

---

## Latest tests

Recorded on the documentation pass that added this file. Re-run and update after code changes.

```text
python3 -m pytest
```

Expected: full suite green (currently **174** tests). Safety audit must stay green.

---

## Known issues

1. **Start vs kill switch.** Home Start does not call `kill_switch.assert_clear()`. System halt therefore does not stop a paper session the way dry-run does. Intentional so Start works with the default engaged halt file — but it is a consistency gap.
2. **Home does not hydrate.** Refreshing the page while a session runs shows STOPPED until Start is clicked again (Start restarts the session).
3. **`status: SIMULATED`** can appear on a running session because `_attach_alpaca_paper` overwrites status when Alpaca is unused. The UI uses `grok` / `running`, not `status`.
4. **No profitability claim.** Benchmarks exist; they are not a licence to say the bot wins.

---

## Next sensible development step

Make the desk **observable and consistent**, still paper-only:

1. On home mount, GET `/api/paper-session` so a running session survives refresh.
2. Optionally thread the file-backed kill switch into `PaperSession` **without** enabling live trading — so System halt and Start agree.
3. Do **not** connect Alpaca as the default fill path. Do **not** enable live.

Do not start a new product (new markets, live money, strategy rewrite) until Start/Stop/status/fail-closed stay boringly reliable.
