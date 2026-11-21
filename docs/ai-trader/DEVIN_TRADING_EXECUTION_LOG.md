# Devin trading execution log

Append-only record of what was inspected, decided, built and found. Simulated
and historical results are labelled as such; none of them is a trading profit.

## 2026-11-21 — Session 1

### Execution state (read-only checks, production database)

| Item | State |
|---|---|
| AI Trader (`ai-trader`) | Paper only. `LIVE_TRADING_ALLOWED = False`; `TRADING_MODE=simulate` on Render; no live adapter is reachable. |
| PeakSwift Big `sol-100` (Solana) | Trial row: `shadow`, paused, kill switch on, no owner approval. Live state row auto-paused `owner_disarmed`, zero balances. No rows in `trading_sol_orders`. |
| PeakSwift Big `btc-100` (Kraken) | `shadow`, running, kill switch off. Shadow places no orders. |
| Wallets | Two verified Solana mainnet wallets (`owner`, `trader`). No keys were read. |
| Not verifiable from here | Whether the PeakSwift worker host still has `TRADING_LIVE_ARM` or a trader key set. Owner to confirm both are unset. |

Nothing was changed in production. No transaction was sent.

### Ownership

- `ai-trader`: self-contained paper agent (Python `DeskWorker`, SQLite + checkpoint, Render worker, Vercel UI). Market data: Coinbase public candles.
- `peakswift_big-/trading-engine`: separate engine (`peakswift_trader`) with paper desk, Kraken and Solana adapters, Postgres (Neon) `trading_*` tables. This is the only code that can sign a transaction, behind owner approval + `TRADING_LIVE_ARM` + a proven trader key.

### Decision: build the research lab first

Both repositories already record that no tested strategy beats costs. Paper
trading or a live pilot of an unproven rule would only produce noise, so the
first deliverable is a lab that can reject strategies honestly.

### Built: `ai_trader.research` (stdlib only)

- `data` — public Binance candles, completed bars only, validated (OHLC sanity, duplicates, alignment; gaps reported, never filled), SHA-256 recorded in every report.
- `strategies` — nine fixed candidates in six families plus buy-and-hold. Grids were written before any result was seen.
- `backtest` — long/flat, no leverage, decision on close, fill at next open, fee 0.25% + slippage 0.05% per side, 2x stress case, book closed at cost at the end of each window.
- `walkforward` — expanding-window parameter selection, stitched out-of-sample folds, final 20% held out.
- `metrics` — return, drawdown, Sharpe/Sortino, trade statistics, block-bootstrap test against buy-and-hold at the same exposure, deflated Sharpe across all configurations tried.
- `qualification` — the gate: `REJECTED` / `INSUFFICIENT_EVIDENCE` / `PAPER_CANDIDATE` / `LIVE_PILOT_ELIGIBLE`. History alone can never return `LIVE_PILOT_ELIGIBLE`; that needs 60 days and 20 trades of forward paper trading, and still requires owner approval.
- CLI: `python -m ai_trader research fetch|run [--holdout]`.

Tests (`tests/test_research_lab.py`) check that no rule reads future bars, fills
happen on the next open, costs are charged exactly, exposure stays in [0, 1],
the holdout is not read unless asked, and a random walk earns no timing edge.

### Gate changes made after the first run (both stricter)

1. Windows now close the open position at cost. Before this, a trade still open at a fold boundary was missing from trade statistics.
2. A breached drawdown limit or a net loss is `REJECTED` even when the trade count is low. More trades cannot undo an observed 50% drawdown.

### Result: development run, daily candles, 2017-08 to 2026-11 (simulated)

Full tables: `research/development_report.md`. Out-of-sample folds only.

- **No strategy qualified on any asset.** 0 of 27 are `PAPER_CANDIDATE`.
- **No strategy shows a timing edge.** After correcting for nine candidates, none beats buy-and-hold held at the same average exposure.
- Trend rules (EMA cross, momentum, breakouts) made money on BTC and ETH while the asset itself rose. They cut drawdown from about 77% to 37-62%, still above the 25% limit.
- Mean reversion lost money on BTC after costs.
- Closest on BTC: `volume_breakout` (+1198% vs +970% buy-and-hold, drawdown 38%, 16 trades, uncorrected p = 0.01). Fails drawdown, trade count and the multiple-testing correction.
- Lowest-drawdown rules (`vol_target_trend`, `trend_ensemble` on SOL, about 21%) have under three years of out-of-sample data.

Limits of this run: one exchange's candles, daily bars only, ETH and SOL chosen
with hindsight (survivorship), no order-book liquidity model beyond the flat
slippage figure.

### Decision: the holdout was not scored

Nothing passed development, so scoring the holdout would spend it for no
decision. It stays untouched for the next set of candidates. Any future
`--holdout` run is appended to `research/HOLDOUT_LEDGER.jsonl`.

### Live readiness

Not ready. No strategy has passed historical qualification, so there is
nothing to propose for a live pilot. No allocation is proposed.

### Next

1. Forward paper tournament in the worker: every candidate runs on live public data with its own paper book, heartbeat and leaderboard, so forward evidence accumulates without a browser.
2. Second research round, declared before running: 4h bars for more trades, a portfolio drawdown brake as a risk overlay, cross-asset rotation.
3. Owner: confirm `TRADING_LIVE_ARM` and the trader key are unset on the PeakSwift worker host.

### Review fixes to the lab (PR #6)

Four accounting defects found in review were fixed: final-bar exposure, the
benchmark's first-bar return, stitched cost totals, and a holdout loss that
was not rejected when development evidence was thin. The development report
was regenerated; no verdict changed.

### Built: forward paper tournament (`ai_trader.research.tournament`)

- `tournament_registration.json` froze one parameter set per strategy and asset on 2026-11-21 11:38 UTC, with the SHA-256 of the history used. The first scored bar is the 2026-11-22 daily candle, the first to open after registration.
- The leaderboard is recomputed from that file and public candles, so it needs no browser, no always-on process and no database. A restart cannot lose or duplicate a paper trade.
- It fails closed: stale candles, history that no longer matches its hash, or a missing registration return no leaderboard.
- Served by the worker at `GET /api/tournament` (read-only, cached 15 minutes, reports last success and last error) and by `python -m ai_trader research tournament`.
- The paper gate needs 60 days, 20 trades, a net gain and drawdown under 15%. Passing it is not enough on its own: the strategy must also have passed historical qualification, and none has.

Limits: fills are assumed at the next daily open with fixed costs, so this
measures the rules, not execution quality. The registration is never
overwritten; a new tournament needs a new file and a logged reason.

State at registration: 30 entries (9 strategies + buy-and-hold on 3 assets), 0 forward bars, all provisional.

### Re-registered the tournament before any bar was scored

The first registration (11:31 UTC) would have scored the 2026-11-21 candle,
whose open was already 11.5 hours old, so the first paper fill would have been
at a known price. Scoring now starts at the first candle that opens after
registration, and a gap in forward candles returns no leaderboard. The file
was replaced at 11:38 UTC with the same candle history; no forward bar had
been scored, so no result was discarded.

### Research round 2 (4-hour bars, drawdown brake, cross-asset rotation)

Candidates and grids were committed (`8c4ba1d`) before the first run on real
data. Round 1's candidates and the tournament registration were not changed.
The qualification gate is unchanged; the timing test is corrected for all 18
candidates tried across both rounds. The final 20% holdout was not scored.

Result (simulated, historical, not profits): 25 strategy/market evaluations,
22 rejected, 3 with insufficient evidence, 0 qualified. Full tables:
`docs/ai-trader/research/round2_development_report.md`.

- 4-hour bars did not help: every 4-hour candidate still had a 44-75% drawdown.
- The drawdown brake cut the ensemble's drawdown to 25-35%, still over the 25% limit.
- The low-volatility ensemble met the drawdown limit (9-18%) by being invested
  only 13% of the time; it has too few trades and no timing edge after correction.
- Rotation did not beat holding the same assets equally (timing p 0.19-0.48).

Known limits: BTC and ETH 4-hour data has 8 gaps (16 missing bars), reported
and not filled. Three assets is a thin basis for rotation.

Live readiness: unchanged, not ready. No candidate from this round is proposed
for the tournament.
