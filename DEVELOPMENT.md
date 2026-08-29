# Development rules

How humans and AI assistants should change AI-Trader.

Repository: [charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader)  
Read [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md) first.

---

## Default stance

Paper trading first. The product is a research desk, not a broker.

If a change is not required to finish the requested task, do not make it.
Do not rewrite working modules. Do not restyle the home screen unless asked.

---

## Hard invariants

These are not style preferences. Breaking them is a bug.

1. **`LIVE_TRADING_ALLOWED` stays `False`** in `src/ai_trader/safety.py`.
   There is no env override and no “just for a test” exception.
2. **Never connect Alpaca live.** `https://api.alpaca.markets` must remain
   blocked. Paper host only: `https://paper-api.alpaca.markets`.
3. **Never bypass the risk engine.** Every proposed BUY/SELL goes through
   `RiskEngine`. Do not let Grok, the UI, or a session runner call a broker
   or the paper ledger directly.
4. **`allow_orders=False`** stays the constructor default.
5. **Kill switch** stays file-backed and defaults engaged. Clearing it must
   not enable live trading.
6. **Starting cash is £100** (`STARTING_CASH` in
   `src/ai_trader/account/simulated.py`) unless the user explicitly asks to
   change it.
7. **No secrets in git.** No `.env`, API keys, tokens, passwords, or
   `credentials.json`. `.env.example` may contain empty placeholders only.
8. **Grok does not talk to brokers.** `GrokAnalyst` proposes JSON
   BUY/SELL/HOLD. It must not send tools or call Alpaca.

`tests/test_safety.py` and `tests/test_safety_audit.py` exist to catch
regressions. If you change safety, those tests must still pass for the
right reasons — do not weaken them.

---

## Paper session behaviour to preserve

Unless the user asks otherwise:

- Start = BTC-USD, public Coinbase, 5-minute **completed** bars, continuous.
- Sequential walk, **no look-ahead**.
- Grok (or fixture HOLD) on warmup 8, then every 8 bars.
- Fills at the **next bar open** ± spread/slip (`paper/execution.py`).
- Stop cancels pending and blocks new paper trades.
- UI Start/Stop stay on the home screen; do not hide them behind System.

---

## How to modify safely

1. State the smallest change that satisfies the request.
2. Touch only the modules on that path (see ARCHITECTURE.md).
3. Keep public function names and JSON status fields the UI already reads
   (`grok`, `running`, `balance`, `today_pnl`, `current_decision`, `position`,
   `broker`, `live`, `data_error`).
4. Add or extend a test in `tests/` for the behaviour you changed.
5. Run the full suite:

   ```bash
   PYTHONPATH=src python3 -m pytest
   ```

6. If you changed TypeScript, also run `npx tsc --noEmit`.
7. Click Start (or POST `/api/paper-session/start`) and confirm:
   - `live` is false
   - `broker` is `NOT USED` unless Alpaca paper is explicitly under test
   - `balance` starts at 100
   - `LIVE_TRADING_ALLOWED` is still False
8. Commit source + tests. Do not commit `node_modules`, `data/*.db`, logs,
   screenshots, `.vercel`, or `.env`.

---

## Never enable live trading without an explicit milestone

A future live-trading milestone would need **all** of:

- A written user request that says live trading, not “paper” or “Alpaca”
- A dedicated safety review
- Keys that are not this repo
- The current kill switch, risk engine, and paper simulator left in place as
  the default path

Until that milestone exists: do not add a live flag, a live URL, a live
submit path, or a UI control that implies real money.

---

## Secrets

- Read keys from the environment (`XAI_API_KEY`, `ALPACA_*`) in server/Python
  only.
- Never put keys in `VITE_` variables (those ship to the browser).
- Never print secrets in logs, RPC JSON, or dashboard payloads
  (`Settings.public_view()` is the safe snapshot).
- If a secret is committed by mistake, rotate it. Do not just delete the file.

---

## Tests before commit

Minimum:

```bash
PYTHONPATH=src python3 -m pytest
```

If the change is in the Start path, also:

```bash
curl -sS -X POST http://127.0.0.1:8080/api/paper-session/start \
  -H 'content-type: application/json' \
  -d '{"symbol":"BTC-USD","source":"public","bars":24,"timeframe":"5m","grok_frequency":8,"warmup":8,"continuous":true}'
```

Expect `grok: RUNNING`, `live: false`, `broker: NOT USED`, `balance: 100`.

Then stop:

```bash
curl -sS -X POST http://127.0.0.1:8080/api/paper-session/stop
```

---

## What not to do

- Do not start a second HTTP server for the paper engine. Stdio RPC only.
- Do not bind Python to the preview port (it steals the UI).
- Do not fetch random extra hosts from the Node process.
- Do not “simplify” by merging risk into Grok or Grok into the simulator.
- Do not add auth or a database for accounts unless asked (this app is auth-off).
- Do not commit AGENTS.md, `.grok/`, `screenshots/`, or other sandbox junk
  (already gitignored).

---

## Suggested first files for an AI clone

```text
DEVELOPMENT.md
ARCHITECTURE.md
README.md
src/ai_trader/safety.py
src/ai_trader/pipeline/orchestrator.py
src/ai_trader/session/runner.py
src/lib/paper-engine.server.ts
src/components/trading-home.tsx
tests/test_safety_audit.py
```
