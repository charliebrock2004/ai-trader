# AI-Trader

An autonomous **paper** trading agent that starts with £100 of simulated capital
and tries to find out whether a real edge exists in event-driven prediction
markets.

**Live trading is disabled in code.** There is no live mode, no real-money
broker, and no environment variable that could create one.

| | |
|---|---|
| Capital | £100 paper, GBP base currency |
| Strategy | Event-driven: official data release → deterministic edge → prediction-market contract |
| LLM role | Skeptic. It argues against trades; it cannot make one. |
| Live trading | `LIVE_TRADING_ALLOWED = False` |
| Tests | 453 Python + 12 frontend |
| Edge demonstrated | **No.** Zero completed trades. |

This repository is public. Never put API keys in it.

---

## The idea

Most "AI trading bots" ask a language model which way the market will go. That
is unfalsifiable and usually unprofitable.

This one does something narrower and testable. When an official statistic
publishes — a CPI print, say — a prediction-market contract that resolves on that
number becomes, briefly, a question with a knowable answer. If the market's price
has not caught up with the published figure, there is an edge, and it can be
computed rather than guessed.

So the probability is calculated in Python from the published number and the
contract's own resolution rule. The model's job is different: it is handed a
trade that has already been priced and sized, and asked *why this is wrong* — the
bear case, the resolution-rule traps, the ways the edge could be illusory. It can
veto. It cannot buy.

**Whether this edge actually exists is an open question.** The system is built to
answer it honestly, including by answering "no".

---

## Survival

The agent has finite capital and a real end state.

```
£100  HEALTHY      full permitted risk, still needs a 5-point edge
 £85  CAUTION      0.6× size, 8-point edge
 £70  DEFENSIVE    0.3× size, 12-point edge
 £55  CRITICAL     0.1× size, 20-point edge
 £40  TERMINAL     dead — permanently, irreversibly
```

The important part is the direction. Losing money makes the agent *more*
conservative, never less — no "I need to win it back" behaviour is reachable,
because the configuration refuses to construct a state where a worse position
permits more risk, and property tests assert it across every state pair.

`TERMINAL` is one-way. It is written to a file and to the database, so losing
either does not resurrect the agent, and there is no method anywhere that clears
it. Reviving a dead agent requires a human editing both by hand.

The agent tracks its own operating costs and runway, and can report that it is
running out of money. It cannot act on that: cost is never an input to sizing or
to the edge threshold, and a test asserts the guardian's verdict is unchanged by
arbitrary accrued cost.

---

## How a decision is made

```
release calendar
  → official value, read twice, both reads must agree
  → deterministic probability from the published number
  → market probability (the ask, not the mid)
  → edge = model − market − fees − spread
  → cheap deterministic filtering, every rejection recorded
  → analyst challenge: bull case, bear case, invalidators, PROCEED or PASS
  → policy guardian: can only downgrade
  → risk: sized from the whole premium, because a binary has no stop
  → depth-aware paper fill against the observed book
  → ledger → settlement → Brier score → calibration
```

Anything unclear at any stage means HOLD. Every decision is recorded — including
the HOLDs, which are the majority — with its inputs and its reason.

---

## Running it

Python 3.10+ and Node 22.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm install

cp .env.example .env
export AI_TRADER_API_TOKEN="something-long-and-random"
npm run dev          # http://localhost:8080
```

Locally, the Node server spawns the Python worker itself over stdio. Deployed,
the worker is its own always-on service and the Node server proxies to it over
HTTPS — same engine, same commands, different transport. See
[DEPLOYMENT.md](DEPLOYMENT.md).

Without `AI_TRADER_API_TOKEN` the read-only pages work and every mutating
endpoint refuses — an unconfigured deployment is closed, not open.

```bash
python3 -m pytest      # 482 tests
npm test               # frontend contract tests
npx tsc --noEmit
```

---

## Screens

| Route | Shows |
|---|---|
| `/` | Is it alive, what has it got, what is it doing, why did it decide that. The survival meter. |
| `/decisions` | Every decision, rejections included, with reasons. |
| `/decisions/:id` | The black box: inputs → probabilities → edge → bull/bear → verdicts → outcome → was it right. |
| `/performance` | Equity, calibration, and an honest note on what the sample supports. |
| `/system` | Component health. Goes red when something is red. |

Every number comes from the engine. There is no fallback snapshot and no
`localStorage` state: if the engine is unreachable, the UI says so rather than
showing a stale figure.

---

## What it cannot currently do

**It cannot trade, because no venue is connected.** The contract ladder is built
and priced, but there is no order book attached, so every contract reports "no
order book" and the agent holds. That is correct fail-closed behaviour and it is
visible on the System page.

Connecting a venue means implementing `PredictionMarketAdapter` against it and
passing its `book_source`. Nothing else in the pipeline changes.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the full list of what is and is
not done.

---

## Honest claims

- No strategy here has a demonstrated edge. There are zero completed trades.
- The performance page states what the sample can support, and says "nothing"
  when that is the answer.
- Costs are a real obstacle at this size: at 50c the entry fee alone is 1.75
  probability points before spread. `break_even_edge()` makes that measurable.
- Turning £100 into a large number is not a plan, a projection, or a promise.
  It is the question the experiment exists to test.

---

## Documentation

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — what works, what does not, what is untested
- [ARCHITECTURE.md](ARCHITECTURE.md) — module map and where the safety lives
- [DEVELOPMENT.md](DEVELOPMENT.md) — invariants and how to change things safely
- [DEPLOYMENT.md](DEPLOYMENT.md) — why this cannot be serverless
