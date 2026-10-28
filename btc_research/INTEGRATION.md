# Integration contract

Give this file to the developer integrating the module into AI Trader / PeakSwiftBig.
The module is intentionally ignorant of that application's auth, database,
frontend and deployment.

## What this is

A read-only research library. It answers: on public BTCUSDT spot history, do
any of a fixed list of baselines have a positive expected return after costs,
out of sample, under a pre-registered gate?

It does **not**:

- place or cancel orders
- read API keys, secrets, seed phrases or `.env` files
- import `ai_trader`, PeakSwiftBig, a database, or an auth package
- enable live trading
- claim that a backtest is paper trading or live performance

AI Trader's existing rule still applies: deterministic code decides anything
that could touch money, and nothing in this package is allowed to touch money.
The LLM must not be given these results and asked to invent a trade.

## Where it lives

```
btc_research/
  pyproject.toml
  README.md
  INTEGRATION.md
  fixtures/btc_sample_1h.csv
  src/btc_research/     # the package
  tests/
  outputs/latest/       # generated, not required to import the library
```

Suggested placement inside AI Trader: keep it as a top-level `btc_research/`
directory, or install it as a path dependency. Do not merge it into
`src/ai_trader/`. Do not point it at the paper ledger or the survival database.

## Python entry points

```python
from btc_research import run_study, StudyConfig, CostModel, ExecutionConfig
from btc_research.pipeline import evaluate_candles

summary = run_study(StudyConfig(
    symbol="BTCUSDT",
    interval="1h",
    start="2019-01-01",
    cache_path="data/cache/BTCUSDT_1h.csv",
    out_dir="outputs/latest",
))
```

`run_study` downloads or reuses the cache, writes the bundle, and returns the
summary dict.

`evaluate_candles(candles, ...)` is the pure function. Same candles and same
config produce the same summary, apart from `generated_at_utc` unless the
caller passes `generated_at`.

Command line:

```bash
PYTHONPATH=src python3 -m btc_research run --start 2019-01-01 --out outputs/latest
```

Exit code 0 means the bundle was written, not that an edge was found.
Read `verdict.code`.

## Dependencies

- Python >= 3.10
- Standard library only. No pandas, numpy, scipy, or exchange SDK.
- Network only inside `btc_research.data.binance`, and only for public klines.

## Input schema

Candles, after validation:

| field | type | meaning |
|---|---|---|
| open_time_ms | int | UTC open, milliseconds |
| open, high, low, close | float | quote per 1 BTC, must be > 0 |
| volume | float | base volume, >= 0 |
| close_time_ms | int | must be after open |

CSV cache columns, in order: `open_time_ms,open,high,low,close,volume,close_time_ms`.

Intervals: `1h` (the study that was run) and `1d` (supported by the loader; the
pre-registered windows are defined in hours).

Validation policy:

- Time going backwards raises `DataIntegrityError`. It is not sorted silently.
- Identical duplicate timestamps are counted and dropped.
- Same timestamp, different prices: `DataIntegrityError`.
- Bad OHLC is counted and dropped, not repaired.
- Gaps are counted and left empty. No forward-fill.

Execution config:

| field | default | constraint |
|---|---|---|
| fee_bps | 10 | >= 0, per side |
| slippage_bps | 1 | >= 0, per side |
| half_spread_bps | 0.5 | >= 0, per side |
| initial_equity | 10000 | > 0 |
| max_exposure | 1 | in [0, 1]. Above 1 is rejected. No leverage. |

Costs are assumptions (Binance spot VIP 0 taker is 10 bps; BNB discount is the
sensitivity at 7.5 bps). They are not fetched from a private account.

## Decision time

`targets[t]` is the fraction of equity to hold, in [0, 1], executed at the
**open** of bar t. The strategy may use completed bars `candles[:t]` and the
clock `candles[t].open_time_ms`. It may not use bar t's prices or any later bar.
The simulator then marks to market at the close. Cash cannot go negative.
Buys are cut to the cash available after fees.

## Output schema

`schema_version`: `btc-research-summary/v1`

Top-level keys:

| key | meaning |
|---|---|
| disclaimer | fixed warning |
| performance_classes | `historical_backtest=present`, `paper_trading=not_run`, `live_trading=not_run` |
| data | coverage, gaps, source host |
| costs | primary and sensitivity |
| gates | the pre-registered pass rule, in words and constants |
| full_sample | descriptive backtests. Not evidence of an edge. |
| ranking | walk-forward OOS comparison. This is the table to show. |
| temporal | 24 UTC hours and 7 weekdays, with BH and Bonferroni p-values |
| bayesian | one-shot P(mean > 0) on OOS bar returns |
| sizing | quarter-Kelly, cap 25%, zero if the train window has no positive edge |
| regimes | pre-registered calendar slices |
| walk_forward_folds | one row per strategy per fold |
| verdict.code | `insufficient_evidence`, `insufficient_sample`, or `oos_gate_passed` |
| verdict.promising | names that passed. Empty means no edge was established. |
| curves | downsampled equity and drawdown for the chart |

A strategy passes only if, on the primary cost: at least 5 OOS folds, at least
60% of folds profitable, the HAC 95% interval of the OOS mean bar return is
entirely above zero, Bayesian P(mean>0) >= 0.95 under a Normal(0, 0.10^2)
annual-mean prior, the OOS p-value survives Bonferroni across the 9 candidates,
and the final 30% holdout agrees. Buy-and-hold is the benchmark and is not a
candidate.

Files written next to `summary.json`:

- `report.md`
- `ranking.csv`, `strategy_comparison.csv`
- `temporal_hours.csv`, `temporal_weekdays.csv`
- `walkforward_folds.csv`, `regimes.csv`, `bayesian.csv`
- `equity_curves.csv`, `equity_drawdown.svg`

## Error handling

| exception | when |
|---|---|
| `btc_research.exceptions.DataSourceError` | download failed or returned almost nothing |
| `btc_research.exceptions.DataIntegrityError` | time runs backwards, or a timestamp disagrees with itself |
| `ValueError` | bad config, exposure outside [0, 1], leverage requested |

Do not catch these and replace them with an empty "no trades" dashboard.
Show the error. An unreachable data file is not a flat equity curve.

## Tests

```bash
cd btc_research
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

Covers look-ahead, fees, fractional Kelly (including a zero when the edge is
not positive), chronological rejection, duplicate and gap handling,
reproducibility, losing trades and drawdown. No network.

## How PeakSwiftBig should use it

After login, the AI Trading workspace can **read** `summary.json` and render it.
That is a display of a research artifact.

Do not:

- call `run_study` from a browser request on every page view (it is a batch job)
- let a user click "pass the gate" to change position size in the paper desk
- feed the summary to a model that is allowed to emit orders
- store the candles in the production auth database
- treat `verdict.code == oos_gate_passed` as clearance for live capital

A sane integration is a scheduled offline job that writes `summary.json` into
object storage, and a read-only panel that shows `verdict`, `ranking`, and the
disclaimer. The paper desk keeps its own survival engine and its own ledger.

## Position sizing boundary

`btc_research.sizing.fractional_kelly` is a pure function. It returns 0 when
the conservative edge is not positive. Cap it at `max_exposure <= 1`. There is
no martingale helper. If the paper desk wants a size, it should call this and
then still run that size through AI Trader's existing risk and survival
guardian. This module does not replace that guardian.
