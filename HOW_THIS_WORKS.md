# How this works (plain English)

Updated at the end of each phase of the build plan. Last update: end of Phase 0,
22 November 2026. For detail see [PROJECT_STATUS.md](PROJECT_STATUS.md).

## What it is

A program that practises trading Bitcoin with pretend money. It starts with a
pretend £100, watches real public Bitcoin prices, and records what it would
have bought and sold. It is a research project. Nothing here promises a profit,
and the measured results so far claim no edge.

## The money path today

**There is no real money anywhere in this system.**

- It holds no exchange account, no API key that can trade, and no wallet.
- "Buying" means writing a row in its own ledger (a SQLite file on the worker).
  The only broker in the code is an internal simulator. The adapter for an
  outside broker (Alpaca) was deleted in Phase 0.
- Live trading is switched off by a constant in the code
  (`LIVE_TRADING_ALLOWED = False`). No setting, request or environment variable
  can turn it on; changing it needs a code change and a reviewed pull request.
- The only money it can spend is on the optional AI reviewer (xAI API calls),
  capped at 8 calls a day, and only if `GROK_PAPER_ANALYSIS=true`.

## Who decides what

1. **Python code** reads prices, spots a possible trade, and works out the
   size. This is fixed arithmetic, not AI.
2. **The policy guardian and risk engine** can shrink or block that trade. They
   can never enlarge it.
3. **The AI reviewer (Grok)**, when switched on, sees a trade the code already
   chose and can only say "hold". It never picks what to trade, how much, at
   what price, or where.
4. **The simulator** fills the pretend order and updates the ledger.

If any input is missing (no price, no exchange rate, a failed check), the
system does nothing rather than guess.

## Where it runs

- The dashboard is a website on Vercel. It only shows status and has Start and
  Stop buttons.
- The engine is one Python worker on Render's free tier. It sleeps when idle,
  so it runs in bursts, not continuously. Fixing that is Phase 2.

## What is parked or frozen

- **US inflation (CPI) prediction markets:** the code is kept but switched off
  (`EVENT_PIPELINE_ENABLED=false`), because as of November 2026 there is no
  venue a UK resident can legally use for those contracts.
- **The older trader in `peakswift_big-/trading-engine`:** frozen. This
  repository is the only trading project being developed.

## What has to happen before any real money

Not started. The plan requires, in order: real venue fees in the simulator, an
always-on worker with backups, a strategy written down before it is tested, a
read-only exchange connection that reconciles every cycle, and a five-key
safety layer with failure drills. Each needs the owner's explicit approval.
