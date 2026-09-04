# AI-Trader — project status

**Source of truth. Read this first**, then [ARCHITECTURE.md](ARCHITECTURE.md),
[DEVELOPMENT.md](DEVELOPMENT.md) and [DEPLOYMENT.md](DEPLOYMENT.md).

| | |
|---|---|
| Repository | [charliebrock2004/ai-trader](https://github.com/charliebrock2004/ai-trader) (public) |
| Product | Autonomous **paper** trading agent. Not a broker. |
| Capital | £100 paper, GBP base currency |
| Live trading | `LIVE_TRADING_ALLOWED = False`. No live path exists. |
| Engine | One Python `DeskWorker`. The browser is control/monitoring only. |
| Transport | HTTP (`python -m ai_trader http`) for deployment; stdio RPC locally. Same engine, same commands. |
| Hosting | Frontend on Vercel, worker on Render free (sleeps; ledger checkpointed to GitHub) |
| Current strategy | BTC-USD public 5m multi-setup scored detector (long only) + BLS CPI event family (HOLDs until a venue book exists) |
| Tests | 528 Python tests passing |

If this file disagrees with the code, the code wins. Update it after every
significant change, and never mark something complete that is not tested.

---

## What the agent is

A deterministic pipeline with an LLM in exactly one seat — as a skeptic, not a
trader:

```
release calendar → official data (read twice, must agree)
  → deterministic probability → market probability → edge net of fees & spread
  → cheap deterministic filtering → ranked shortlist
  → analyst challenge (bull / bear / invalidators / PROCEED|PASS)
  → policy guardian (downgrade-only)
  → contract risk (sizes from the whole premium)
  → depth-aware paper fill → contract ledger
  → settlement → outcome + Brier score → calibration
  → survival state
```

Every stage can only refuse more firmly than the one before it.

The operational Start path today is the **spot paper session** (Coinbase
BTC-USD, 5-minute bars) running inside the same worker, on the same £100 GBP
book, through the same risk engine. Event-driven prediction-market fills still
HOLD because no venue order book is attached. That is fail-closed, not a fake
market.

---

## What works, and is tested

**Start / Stop / worker**
- Open the app: £100, STOPPED, HOLD.
- Start talks to the Python worker, not the browser. The request returns
  immediately with STARTING; the worker loads market data on its own thread.
- Once candles and FX are in, state becomes RUNNING and cycles continue.
- Stop sets `desired_running=0`, blocks new trades, keeps history.
- A process restart recovers a session that was asked to keep running.
- A TERMINAL agent cannot be started. Live trading cannot be started.
- Every HOLD is written to SQLite with a reason.

**Accounting**
- One base currency (GBP). Instrument prices carry their own quote currency and
  every crossing needs an explicit FX rate. A foreign position without a rate is
  **refused**, not valued 1:1.
- Realised P&L includes the FX move, because that is real money.
- Ledger invariants hold under randomised activity: equity equals cash plus
  invested value, cash never goes negative, total P&L equals realised plus
  unrealised.

**Risk**
- Spot sizing applies three caps and takes the smallest: risk budget,
  concentration (25% of equity), and cash. Sizing assumes the stop can fill
  1.5× the stop distance away, because a stop is not a guarantee.
- Long stops fill at `min(bar_open, stop)`, so a gap through the stop is
  realised. Take-profits never fill on a favourable gap.
- One round trip counts as one trade.
- Binary contracts size backwards from the **whole premium** — there is no stop
  loss on a binary — with caps on per-position premium, total exposure,
  per-event exposure, cash, book depth and contract count.

**Survival**
- `HEALTHY → CAUTION → DEFENSIVE → CRITICAL → TERMINAL` on equity, with
  hysteresis. Worsening is immediate, recovery is slow. Asymmetric on purpose.
- **Losses can never increase permitted risk.** `SurvivalConfig` refuses at
  construction to build a policy where a worse state allows more size or a
  smaller edge, and property tests assert monotonicity across every state pair.
- `TERMINAL` is one-way. Written to a file *and* the database, so losing either
  does not resurrect the agent; an unreadable latch still reads as dead; there
  is no clear/reset/revive method and a test asserts none exists by any name.
- The Policy Guardian can only downgrade. It raises if it ever returns something
  more aggressive than it received, and a 500-case fuzz asserts the property.

**Costs**
- Model tokens at the published rate, hosting, data and fees. Burn and runway,
  where runway counts only capital above the terminal threshold.
- **Cost pressure cannot change trading.** The guardian's verdict is
  byte-identical before and after £50 of accrued cost, and an AST walk asserts
  no cost/runway/burn identifier appears in the guardian at all.

**Event data**
- BLS CPI, read twice over different windows; both reads must agree before a
  value is VERIFIED. A single successful read is UNVERIFIED and not tradeable.
- Refuses: pending, conflicting, malformed, non-numeric, wrong-series,
  unsuccessful, timed-out and unreachable. Every case means HOLD.

**Edge**
- Probability is computed in Python from the published number and the contract's
  own resolution rule, discounted for revision risk. **The LLM never computes
  it.** Confidence returned by the model is treated as a label, not a
  probability.
- `edge = model − market − fees − spread`, computed against the **ask**, not the
  mid. The fee model follows the venue's published `p(1−p)` formula, which peaks
  at 50c — exactly where most trading happens.

**Execution**
- Fills walk the observed book, cross the spread, and fill partially when depth
  runs out. Limits are never paid through. Idempotency keys prevent double-fills.

**Audit trail**
- Every decision is recorded, including HOLDs and filtered candidates, with the
  reason and the inputs attached. A test reconstructs a complete trade from the
  database alone and answers all six audit questions.
- Brier score and correctness are computed on write, never supplied.

**Replay**
- Tapes record inputs, not outputs, and replay feeds them through the identical
  code path. Two replays of one tape produce identical decisions, positions and
  cash. Replay sources hold no HTTP client and refuse to fall back to live data.

**Security**
- Mutating endpoints require a shared secret in production. Grok Build preview
  allows Start/Stop without a token because the operator is the only client.
  Bodies are validated against an allow-list at the HTTP boundary *and* in Python.
- External market and contract text is sanitised before reaching the prompt, and
  the analyst's response schema has no field that could name a ticker, size,
  price or venue.

---

## The strategy the desk actually runs

`ai_trader/strategy/signal.py`. Long-only, on 5-minute BTC-USD.

**Regime** is classified first and named on the dashboard: TREND_UP,
TREND_DOWN or RANGE, from the separation between EMA20 and EMA50 measured in
ATR, plus the slope of the slow average.

**Setups** are the ways a long entry can legitimately arise. Several are
checked on every bar; the best-scoring one is the one considered.

| Setup | Regime | Trigger |
|---|---|---|
| `PULLBACK_CONTINUATION` | TREND_UP | Price came back within 0.6 ATR of EMA20 and turned up |
| `MOMENTUM_CONTINUATION` | TREND_UP | Two consecutive higher closes, holding above EMA20 |
| `BREAKOUT` | TREND_UP, RANGE | New 20-bar high on a bar that expanded ≥ 1.2 ATR |
| `RANGE_BOUNCE` | RANGE | Bottom 30% of the range, turning up, RSI ≥ 25 |

**Opportunity score** blends five independent components — trend, momentum,
entry location, volatility fitness and structure — weighted per setup. A setup
that merely matches is not enough; it has to be a good instance of itself.
Selectivity lives in one threshold (`STRATEGY_SCORE_THRESHOLD`, default 0.68),
so it can be measured and tuned rather than hidden in a conjunction of filters.

**Risk geometry** is ATR-based: stop 2.0 ATR, target 5.0 ATR, and an unresolved
position is closed after 4 hours so it stops occupying the desk's only position
slot. The strategy *proposes* a stop; the risk engine clamps it into a band and
remains the only thing that sets it. Position size is still capped by the same
£2 risk budget and 25% concentration limit as before.

### Why the geometry is what it is

One ATR on 5-minute BTC is about 0.22% and a round trip costs about 0.20%
(spread and slippage, both ways), so costs eat roughly a whole ATR per trade.
This is the arithmetic that decides whether a strategy can work at all:

| stop / target | reward | risk | net R:R | break-even win rate |
|---|---|---|---|---|
| 1.5 / 2.5 ATR (previous) | 0.55% | 0.33% | 0.66 | **60.2%** |
| 2.0 / 5.0 ATR (current) | 1.10% | 0.44% | 1.41 | **41.6%** |

The previous geometry needed a 60% win rate merely to break even. That is not a
strategy, and no amount of signal quality rescues it.

### Why the old detector found nothing

It required an exact SMA 10/20 crossover: a point event, true on one bar per
trend. Every hold the live worker recorded was the regime gate, so it never
reached the entry logic. A regime is a *state* and stays true; a trigger inside
it can recur. That is the whole change.

### Measured behaviour

Trade frequency, on a seeded 5-minute path with BTC-like volatility, through
the real pipeline (detector → guardian → risk engine → simulator → ledger):
**4.6 trades/day**, range 3.9–5.2 across eight seeds.

Adverse conditions, 10 days each, four seeds:

| Market | Trades | P&L | Max DD | Buy-and-hold |
|---|---|---|---|---|
| Strong downtrend | 15 | −£1.80 | 2.4% | −£59 |
| Crash then chop | 1 | −£0.21 | 0.7% | −£90 |
| Very quiet | 0.2 | −£0.04 | 0.2% | −£1 |
| Choppy flat | 42 | −£2.36 | 3.9% | −£5 |
| High volatility | 49 | −£2.75 | 6.7% | −£13 |
| Steady uptrend | 56 | +£7.94 | 1.6% | +£120 |

It stands aside in falling markets and preserves capital; it churns in chop; it
badly underperforms simply holding in a trend, which is normal for a
stop-and-target system.

### No edge is claimed

On the only real market data available here — 1200 daily BTC closes, split
60/40 — the strategy **loses money**:

| Window | Trades | P&L | Win rate |
|---|---|---|---|
| In-sample (first 60%) | 18 | −£4.85 | 27.8% |
| Out-of-sample (last 40%) | 9 | −£0.26 | 33.3% |
| Full period | 29 | −£6.16 | 27.6% |

A 27.6% win rate against a 41.6% break-even is negative expectancy. Two caveats
cut in opposite directions and neither rescues it: the fixture is daily rather
than 5-minute, and it is close-only, so the simulator can only resolve a level
on a close through it — which penalises the far target more than the near stop.
The test cannot settle the question. The live paper record is what will.

The strategy is shipped because it produces genuine, explainable candidates at a
workable rate, not because it is known to work.

### Why the desk did not trade

Every hold names itself and is stored in `decisions.rejection`:

```sql
SELECT rejection, COUNT(*) FROM decisions WHERE kind='spot' GROUP BY 1;
```

Detector reasons (`downtrend`, `no_setup`, `score_too_low`, `overbought`,
`too_quiet`, `too_wild`, `poor_reward`, `already_long`) and downstream ones
(`policy_downgraded`, `risk_rejected`) share the column, so the whole pipeline
is visible in one query. A spread of reasons means the market offered nothing;
one reason accounting for almost everything means a gate is mis-set.

---

## What is NOT done, and why

**There is no prediction-market venue adapter, so the event pipeline cannot
currently fill.** The CPI ladder is registered and priced, but no order book is
attached. Every contract reports "no order book" and the agent holds. This is
correct fail-closed behaviour and it is visible on the System page. Connecting a
venue means implementing `PredictionMarketAdapter` against it and passing its
`book_source`; nothing else in the pipeline changes. Do not fabricate a book.

**No edge has been demonstrated.** Zero completed event-driven trades. The
performance page says so plainly rather than showing an encouraging empty state.

**Vercel cannot host the worker, and no longer pretends to.** A serverless
invocation has no `python3` and does not outlive the request. The worker
runs on Render free (`python -m ai_trader http`). The Vercel UI defaults to
`https://ai-trader-elxv.onrender.com`. If the worker is asleep the UI says so
rather than faking RUNNING. The £100 ledger is checkpointed to GitHub so a
wake does not silently reset the book.

**No live venue book, so no event-driven fills.** See above.

**Network access is required for public BTC-USD and FX.** Coinbase candles and
Frankfurter USD→GBP are live public feeds. If either is missing the session
STOPS with a reason. It does not invent prices.

**The pre-release probability model is deliberately weak.** It is not expected to
beat the market's own forecast. The strategy is meant to be right *after* a
release, not before one.

**Out-of-sample evidence does not exist yet.** The benchmark harness is built and
splits by time, but there is no historical corpus loaded.

---

## Known limitations

1. **Costs may dominate at £100.** At 50c the entry fee alone is 1.75 probability
   points before spread. `break_even_edge()` makes this measurable; do the
   arithmetic before assuming the stake is viable.
2. **Equity carries open binaries at cost**, not marked to the book. A thin book
   would otherwise make equity jump on one resting order, and equity drives the
   survival state.
3. **Spot paper and the event agent share one survival meter and one displayed
   £100 book.** Event fills still HOLD without a venue. Spot fills use the
   internal simulator against public BTC-USD candles.
4. **Drawdown is computed from settlements**, not a tick-level equity path,
   because that is what the engine records.
5. **The Alpaca paper adapter is dormant** and observation only. The session path
   traps `submit` on every broker.
6. **The worker runs on Render free, so it trades in bursts, not continuously.**
   It sleeps when idle and only wakes when something pings it. The GitHub
   Actions job that does the pinging is scheduled every ten minutes but GitHub
   throttles scheduled runs hard — the observed rate is roughly every two to
   five hours. On each wake the desk catches up on every candle since the last
   one it processed, so no decision is skipped inside that window, but the
   agent is awake for a minute or two at a time rather than all day.

   The window is 280 five-minute bars, just over 23 hours. A sleep longer than
   that loses candles permanently: the desk never sees them, never decides on
   them, and never records why.

   Evidence therefore accumulates slowly. **A Render Starter instance with a
   1 GB disk removes all of this** — no sleeping, no snapshot round-trip, and a
   genuinely continuous desk. It is the single change that would most improve
   the quality of the evidence.
7. **There is no paid disk.** SQLite and the TERMINAL latch are restored from
   GitHub `worker-endpoint/snapshot.json` on boot, copied there by the Actions
   job. Until the first checkpoint after a deploy, a restart would start a
   fresh £100 book and the UI says so.
8. **The desk cannot go short.** The risk engine refuses shorts outright, and
   that is a safety property: a short has unbounded loss and needs borrow and
   margin accounting this ledger does not have. In a downtrend the desk reports
   TREND_DOWN and stands aside, which is roughly a third of the time. Fixing
   this means building short accounting, not relaxing the risk engine.
9. **It churns in sideways markets.** Trend-following pays costs in chop. The
   RANGE_BOUNCE setup and the breakout-expansion test reduce it; they do not
   remove it.
10. **Page access is the frontend's only gate by default.** Anyone who can load
   the Vercel URL can press Start. The System page states this. Live trading
   is still impossible.

---

## How to run

Deployed (see [DEPLOYMENT.md](DEPLOYMENT.md)):

```
Vercel   PAPER_WORKER_URL + AI_TRADER_API_TOKEN
Render   PYTHONPATH=src python3 -m ai_trader http, disk at /var/data
```

Locally, the frontend spawns the worker over stdio:

```
npm run dev          # UI on :8080, Python worker spawned on first API call
```

Or run the worker exactly as the host does:

```
PORT=8090 AI_TRADER_API_TOKEN=dev-token PYTHONPATH=src python3 -m ai_trader http
```

Python tests:

```
PYTHONPATH=src python3 -m pytest tests -q
```

Frontend tests:

```
npm test
```

---

## Before any real-money consideration

Not the next step, and not a config flip. It would need all of:

- A venue adapter, live, reconciling cleanly for an extended period.
- Months of live paper evidence with a pre-registered success criterion.
- Genuine out-of-sample results on recorded historical data.
- Positive calibration skill, not just positive P&L.
- A separate security review, credentials outside this repository, and
  server-side limits at the venue as well as in code.
- `LIVE_TRADING_ALLOWED` still `False` until that milestone is designed,
  reviewed and explicitly requested.

---

## Next sensible step

1. Keep paper-trading BTC-USD on the deployed worker until there is a real
   decision history measured in weeks, not hours.
2. Connect one real prediction-market venue as a **read-only book source**, so
   the event pipeline can do something other than HOLD.
3. Load a historical corpus so the benchmark harness has out-of-sample data to
   split.
