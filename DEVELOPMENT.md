# Development rules

How humans and AI assistants change AI-Trader.

Read [PROJECT_STATUS.md](PROJECT_STATUS.md) and [ARCHITECTURE.md](ARCHITECTURE.md)
first.

---

## Default stance

Paper only. The product is an experiment designed to find out whether an edge
exists, not a system that assumes one does. If a change makes the results look
better without making them more true, it is the wrong change.

---

## Hard invariants

Breaking any of these is a bug, not a style disagreement.

1. **`LIVE_TRADING_ALLOWED` stays `False`.** No env override, no "just for a
   test".
2. **Losses must never increase permitted risk.** Every survival state's
   ceilings are monotone. `SurvivalConfig` refuses to construct otherwise, and
   property tests assert it. If you add a state or a ceiling, extend those tests.
3. **The Policy Guardian may only downgrade.** `_outcome` raises on an upgrade.
   Do not remove that assertion.
4. **TERMINAL is one-way.** Do not add a method that clears it. Do not make the
   database the only witness, or the file the only witness.
5. **Operating cost may never reach sizing or the edge threshold.** The guardian
   takes no cost input and a test asserts none appears in its code.
6. **The LLM never produces a probability, a size, a ticker, a price or a
   venue.** If you extend the analyst schema, it must remain incapable of
   expressing an order.
7. **No implicit currency conversion.** A foreign position without an explicit
   FX rate is refused.
8. **A binary's risk is its whole premium.** Never size one as though it had a
   stop.
9. **Every decision is recorded, including HOLDs and rejections**, with a reason.
10. **No secrets in git**, and never in a `VITE_` variable.

---

## What to preserve

The pieces that were already good and should stay that way:

- AI/execution separation and strict response validation.
- Fail-closed market data — timeout, stale, malformed and unavailable all mean
  HOLD.
- No-look-ahead discipline (`_visible_only`, `seen_future`, ordered replay).
- Conservative fill assumptions. Never make a fill more favourable to look better.
- The dark, restrained visual language.
- An honest `PROJECT_STATUS.md`.

---

## How to change something safely

1. State the smallest change that satisfies the request.
2. Touch only the modules on that path.
3. Add or extend a test for the behaviour you changed. A new module without a
   test does not ship.
4. Run everything:

   ```bash
   python3 -m pytest        # 453 tests
   npm test                 # frontend contract tests
   npx tsc --noEmit
   npm run build
   ```

5. Read your own diff adversarially. What would make this wrong?
6. Update `PROJECT_STATUS.md` honestly — including anything you made worse.

---

## Test conventions

Tests are the argument that the system is trustworthy, so they are written to
fail for the right reason:

- **Name the property, not the function.** `test_losing_money_shrinks_the_allowed_premium`,
  not `test_review_2`.
- **Assert the corrected behaviour, never adjust an assertion to match a bug.**
  When behaviour legitimately changes, update the expectation *and* say why in
  the comment.
- **Prefer structural proof where possible.** An AST walk asserting the guardian
  cannot see a cost is stronger than a behavioural test that it currently does
  not.
- **Fuzz the invariants.** Monotonicity, downgrade-only and ledger consistency
  are all property-tested across randomised inputs.
- **A test that can be made to pass by loosening a limit is the wrong test.**

---

## Code ownership

To keep two AI partners from colliding:

| Owner | Paths |
|---|---|
| Claude | `risk/`, `survival/`, `edge/`, `contracts/`, `paper/`, `db/`, `analytics/`, `replay/`, `costs/`, `tests/` |
| Grok | `market_data/`, `analysis/`, `ai/`, `session/`, `pipeline/`, `src/components/`, `src/routes/` |
| Either, with review | `agent/`, `orchestrator.py`, `safety.py`, `markets/`, `events/`, docs |

Never both in one file in one session. Cross-boundary changes get a review from
the other. Whoever touches a module runs the full suite and updates
`PROJECT_STATUS.md`.

---

## Things not to do

- Do not weaken a safety test to make a feature work.
- Do not make the paper simulator more optimistic (better fills, no gaps, no
  fees). That is how a fake edge survives to production.
- Do not call a different random seed "out-of-sample".
- Do not treat an LLM confidence score as a calibrated probability.
- Do not add a third chart to the performance page.
- Do not add a UI control that implies real money.
- Do not reintroduce a second trading engine in TypeScript.
- Do not claim profitability. If the evidence says there is no edge, the
  application must say so — that outcome is a successful experiment.

---

## Running it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm install

cp .env.example .env      # never commit .env
export AI_TRADER_API_TOKEN="something-long-and-random"
npm run dev               # UI on :8080, Python worker spawned automatically
```

Without `AI_TRADER_API_TOKEN`, read-only pages work and every mutating endpoint
refuses. That is deliberate.
