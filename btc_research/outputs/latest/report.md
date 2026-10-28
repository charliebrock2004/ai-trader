# BTC spot research report

No candidate showed a reliable out-of-sample edge after costs.

Historical backtest only. This is not paper-trading performance and not live performance. It is not a recommendation to trade. Costs, fills and the strategy list are assumptions.

## What this is

- Result class: historical backtest.
- Paper trading: not run.
- Live trading: not run.
- No orders were sent. No private API credentials were read.

## Data

- Symbol: BTCUSDT 1h spot.
- Source: rest (https://data-api.binance.vision).
- Coverage: 2019-01-01T00:00:00Z to 2026-10-28T09:00:00Z.
- Valid bars: 68518. Identical duplicates dropped: 0. Malformed dropped: 0.
- Gaps: 20 gaps, 60 missing bars. Missing bars were not filled.
- Cache hit: False.

Public Binance spot klines. The forming candle is dropped. Gaps are left empty. api.binance.com may return HTTP 451 in some regions; the loader then uses data-api.binance.vision and does not invent bars. This is not a full tick history, so fills are bar-open approximations.

## Costs

Primary per side: fee 10.0 bps, slippage 1.0 bps, half-spread 0.5 bps.
Approximate round-trip drag on a zero gross move: -0.23%.
This matches a Binance spot VIP 0 taker (10 bps) plus a small-order spread and slippage allowance. The 7.5 bp BNB-discount case is a sensitivity, not the base case. Maker and taker are the same at VIP 0 on spot, so a limit order does not remove the fee.

## Pre-registered decision rule

A candidate passes only if all of the following are true on the primary cost model: at least 5 walk-forward test folds; at least 60% of those folds have a positive net return; the Newey-West 95% interval for the mean out-of-sample bar return lies entirely above zero; the skeptical Bayesian probability that the out-of-sample mean is positive is at least 0.95; the out-of-sample HAC p-value survives a Bonferroni correction across the 9-candidate family; and the single final 30% holdout also has a positive total return whose HAC 95% interval lies above zero. Buy-and-hold is the benchmark and is not in the family. Failing the gate does not prove that no edge exists.

## Walk-forward out of sample

This is the table that matters. Full-sample results are below it and are not evidence of an edge.

| Strategy | Role | OOS return | Max DD | Sharpe | Folds up | P(mean>0) | CI low | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| momentum_168 | candidate | -63.02% | 86.47% | -0.16 | 37.93% | 0.406 | -0.00% | fail |
| momentum_72 | candidate | -70.78% | 89.47% | -0.23 | 37.93% | 0.370 | -0.00% | fail |
| vol_filtered_momentum | candidate | -74.03% | 87.80% | -0.60 | 31.03% | 0.137 | -0.00% | fail |
| mean_reversion_48_z1.5 | candidate | -91.93% | 93.06% | -0.66 | 20.69% | 0.168 | -0.01% | fail |
| session_europe | candidate | -99.39% | 99.47% | -2.28 | 3.45% | 0.000 | -0.01% | fail |
| adaptive_best_hour | candidate | -99.42% | 99.47% | -4.59 | 0.00% | 0.000 | -0.01% | fail |
| session_us | candidate | -99.47% | 99.56% | -1.69 | 6.90% | 0.006 | -0.01% | fail |
| momentum_24 | candidate | -99.78% | 99.85% | -1.90 | 10.34% | 0.003 | -0.01% | fail |
| session_asia | candidate | -99.90% | 99.90% | -2.96 | 3.45% | 0.000 | -0.01% | fail |
| buy_and_hold | benchmark | 579.40% | 77.31% | 0.74 | 55.17% | 0.785 | 0.00% | fail |

## Verdict

None of the pre-registered candidates cleared the walk-forward, holdout, cost, Bayesian and multiple-testing bar. Buy-and-hold can still be up. That is exposure to BTC, not evidence that a timing rule has an edge. The evidence is insufficient to establish a trading edge.

Promising names under the pre-registered rule: none

## Time-of-day and weekday (multiplicity corrected)

Gross means test whether that clock bucket's average return differs from zero. Net means subtract one full round trip per trade. A rejected null on gross returns is not a tradable edge. p-values are HAC (Newey-West) normal approximations. BH and Bonferroni are computed inside each family (24 hours, and separately 7 weekdays).

### Hours

| Hour | N | Mean gross | Mean net | p raw | p BH | BH 5% |
| --- | --- | --- | --- | --- | --- | --- |
| hour_00utc | 2857 | 0.02% | -0.21% | 0.1189 | 0.4755 | no |
| hour_01utc | 2858 | -0.01% | -0.24% | 0.6389 | 0.8070 | no |
| hour_02utc | 2847 | -0.01% | -0.24% | 0.2891 | 0.6882 | no |
| hour_03utc | 2846 | -0.01% | -0.24% | 0.3685 | 0.6882 | no |
| hour_04utc | 2848 | -0.01% | -0.24% | 0.3854 | 0.6882 | no |
| hour_05utc | 2851 | 0.01% | -0.22% | 0.4221 | 0.6882 | no |
| hour_06utc | 2852 | 0.01% | -0.22% | 0.5145 | 0.7264 | no |
| hour_07utc | 2852 | 0.01% | -0.22% | 0.4301 | 0.6882 | no |
| hour_08utc | 2853 | 0.01% | -0.22% | 0.3691 | 0.6882 | no |
| hour_09utc | 2855 | 0.00% | -0.23% | 0.8111 | 0.8728 | no |
| hour_10utc | 2854 | 0.00% | -0.23% | 0.7158 | 0.8542 | no |
| hour_11utc | 2855 | 0.01% | -0.22% | 0.2699 | 0.6882 | no |
| hour_12utc | 2855 | 0.01% | -0.22% | 0.3827 | 0.6882 | no |
| hour_13utc | 2854 | 0.01% | -0.22% | 0.2591 | 0.6882 | no |
| hour_14utc | 2854 | -0.01% | -0.24% | 0.4748 | 0.7122 | no |
| hour_15utc | 2855 | 0.03% | -0.20% | 0.0504 | 0.3022 | no |
| hour_16utc | 2855 | -0.00% | -0.23% | 0.8728 | 0.8728 | no |
| hour_17utc | 2855 | 0.01% | -0.22% | 0.6127 | 0.8070 | no |
| hour_18utc | 2856 | 0.00% | -0.23% | 0.8640 | 0.8728 | no |
| hour_19utc | 2857 | 0.00% | -0.23% | 0.7474 | 0.8542 | no |
| hour_20utc | 2857 | 0.02% | -0.21% | 0.1011 | 0.4755 | no |
| hour_21utc | 2857 | 0.05% | -0.18% | 0.0010 | 0.0233 | yes |
| hour_22utc | 2857 | 0.03% | -0.20% | 0.0025 | 0.0294 | yes |
| hour_23utc | 2857 | -0.03% | -0.26% | 0.0466 | 0.3022 | no |

### Weekdays

| Day | N | Mean gross | Mean net | p raw | p BH | BH 5% |
| --- | --- | --- | --- | --- | --- | --- |
| Monday | 405 | 0.44% | 0.21% | 0.0101 | 0.0354 | yes |
| Tuesday | 407 | 0.02% | -0.21% | 0.8876 | 0.8876 | no |
| Wednesday | 403 | 0.48% | 0.25% | 0.0081 | 0.0354 | yes |
| Thursday | 406 | -0.28% | -0.51% | 0.0757 | 0.1765 | no |
| Friday | 405 | 0.24% | 0.01% | 0.1539 | 0.2692 | no |
| Saturday | 406 | 0.10% | -0.13% | 0.3218 | 0.4505 | no |
| Sunday | 405 | 0.06% | -0.17% | 0.6559 | 0.7653 | no |

## Full sample, descriptive only

These numbers use the same bars that suggested the question. They are not out of sample.

| Strategy | Return | CAGR | Max DD | Sharpe | Trades | Win rate | Expectancy | Sensitivity return |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| buy_and_hold | 2100.79% | 48.46% | 77.20% | 0.95 | 0 | n/a | n/a | 2101.45% |
| momentum_24 | -99.81% | -55.07% | 99.86% | -1.74 | 3479 | 20.06% | -2.87 | -98.46% |
| momentum_72 | -54.18% | -9.50% | 89.36% | -0.04 | 1764 | 22.05% | -3.09 | 32.07% |
| momentum_168 | -24.10% | -3.46% | 86.32% | 0.11 | 1134 | 22.57% | -2.12 | 49.89% |
| mean_reversion_48_z1.5 | -89.33% | -24.88% | 93.12% | -0.52 | 787 | 66.45% | -11.35 | -82.90% |
| vol_filtered_momentum | -64.31% | -12.34% | 88.74% | -0.39 | 1310 | 24.27% | -4.93 | -21.65% |
| session_asia | -99.91% | -59.22% | 99.92% | -2.76 | 2858 | 37.96% | -3.50 | -99.50% |
| session_europe | -99.62% | -50.99% | 99.63% | -2.31 | 2856 | 38.69% | -3.49 | -97.90% |
| session_us | -99.54% | -49.77% | 99.61% | -1.58 | 2857 | 42.74% | -3.48 | -97.46% |

## Bayesian probability

Prior: arithmetic annualised mean ~ Normal(0, 0.10^2). One update. Effective sample size never exceeds the raw count. This is P(mean per-bar return > 0 | data, prior), not a trading instruction.

| Strategy | N | N eff | rho | P(mean>0) | Posterior annual mean |
| --- | --- | --- | --- | --- | --- |
| buy_and_hold | 62640 | 62640.0 | -0.018 | 0.785 | 7.25% |
| momentum_24 | 62640 | 61290.7 | 0.011 | 0.003 | -22.95% |
| momentum_72 | 62640 | 61345.6 | 0.010 | 0.370 | -2.77% |
| momentum_168 | 62640 | 62600.0 | 0.000 | 0.406 | -1.98% |
| mean_reversion_48_z1.5 | 62640 | 62640.0 | -0.031 | 0.168 | -8.06% |
| vol_filtered_momentum | 62640 | 57700.2 | 0.041 | 0.137 | -7.75% |
| session_asia | 62640 | 62640.0 | -0.013 | 0.000 | -39.13% |
| session_europe | 62640 | 62640.0 | -0.014 | 0.000 | -30.27% |
| session_us | 62640 | 62387.6 | 0.002 | 0.006 | -20.95% |
| adaptive_best_hour | 62640 | 61161.6 | 0.012 | 0.000 | -52.80% |

## Position sizing

Quarter-Kelly, hard cap 25% of equity, zero when the training-window edge is not positive. No leverage, no martingale. In-sample fractions are descriptive. The out-of-sample column resizes each test fold using only that fold's training returns.

| Strategy | In-sample fraction | Folds sized at zero | Mean OOS fraction | OOS return if sized |
| --- | --- | --- | --- | --- |
| momentum_24 | 0.00% | 25 | 0.12% | -1.24% |
| momentum_72 | 0.00% | 17 | 0.78% | -0.08% |
| momentum_168 | 0.00% | 18 | 0.88% | -2.76% |
| mean_reversion_48_z1.5 | 0.00% | 20 | 1.53% | -6.90% |
| vol_filtered_momentum | 0.00% | 20 | 0.68% | -0.09% |
| session_asia | 0.00% | 28 | 0.00% | -0.00% |
| session_europe | 0.00% | 27 | 0.10% | -0.64% |
| session_us | 0.00% | 26 | 0.24% | -1.43% |

## Regimes

Pre-registered calendar slices. Descriptive. A strategy that only works in one regime is not stable.

| Strategy | Regime | Return | Max DD |
| --- | --- | --- | --- |
| buy_and_hold | 2019_to_covid | 114.13% | 53.15% |
| buy_and_hold | covid_to_ath | 742.78% | 54.72% |
| buy_and_hold | bear_to_ftx | -75.71% | 77.05% |
| buy_and_hold | ftx_to_etf | 186.25% | 21.74% |
| buy_and_hold | post_etf | 74.60% | 53.74% |
| momentum_24 | 2019_to_covid | -39.17% | 54.94% |
| momentum_24 | covid_to_ath | -45.33% | 64.12% |
| momentum_24 | bear_to_ftx | -84.57% | 84.66% |
| momentum_24 | ftx_to_etf | -51.53% | 64.87% |
| momentum_24 | post_etf | -92.31% | 92.96% |
| momentum_72 | 2019_to_covid | 57.86% | 40.45% |
| momentum_72 | covid_to_ath | 86.35% | 35.59% |
| momentum_72 | bear_to_ftx | -66.46% | 67.27% |
| momentum_72 | ftx_to_etf | 22.52% | 33.37% |
| momentum_72 | post_etf | -62.19% | 77.81% |
| momentum_168 | 2019_to_covid | 95.32% | 44.85% |
| momentum_168 | covid_to_ath | 26.79% | 61.24% |
| momentum_168 | bear_to_ftx | -63.18% | 64.71% |
| momentum_168 | ftx_to_etf | 49.38% | 30.34% |
| momentum_168 | post_etf | -44.40% | 66.21% |
| mean_reversion_48_z1.5 | 2019_to_covid | -29.30% | 52.05% |
| mean_reversion_48_z1.5 | covid_to_ath | -32.64% | 48.33% |
| mean_reversion_48_z1.5 | bear_to_ftx | -51.48% | 57.18% |
| mean_reversion_48_z1.5 | ftx_to_etf | -5.50% | 17.57% |
| mean_reversion_48_z1.5 | post_etf | -51.21% | 60.49% |
| vol_filtered_momentum | 2019_to_covid | 44.42% | 25.18% |
| vol_filtered_momentum | covid_to_ath | 63.78% | 31.89% |
| vol_filtered_momentum | bear_to_ftx | -47.75% | 51.14% |
| vol_filtered_momentum | ftx_to_etf | -26.20% | 34.13% |
| vol_filtered_momentum | post_etf | -60.91% | 66.97% |
| session_asia | 2019_to_covid | -65.44% | 68.82% |
| session_asia | covid_to_ath | -72.49% | 77.80% |
| session_asia | bear_to_ftx | -79.46% | 80.13% |
| session_asia | ftx_to_etf | -53.44% | 54.20% |
| session_asia | post_etf | -90.14% | 90.45% |
| session_europe | 2019_to_covid | -67.09% | 68.45% |
| session_europe | covid_to_ath | -61.08% | 61.08% |
| session_europe | bear_to_ftx | -61.26% | 61.39% |
| session_europe | ftx_to_etf | -51.12% | 51.70% |
| session_europe | post_etf | -84.43% | 85.73% |
| session_us | 2019_to_covid | -34.41% | 45.09% |
| session_us | covid_to_ath | -36.65% | 64.08% |
| session_us | bear_to_ftx | -70.40% | 71.33% |
| session_us | ftx_to_etf | -45.99% | 55.65% |
| session_us | post_etf | -93.11% | 93.32% |

## Assumptions and limitations

- Spot BTCUSDT only. No futures, no funding, no leverage, no borrow.
- Signal at a bar uses completed bars only. The fill is that bar's open. The close is a mark, not a signal.
- Walk-forward test accounts start flat, so a hold across a fold boundary pays an extra round trip.
- Sharpe ratios annualise with sqrt(bars per year) and are descriptive. Crypto returns are fat-tailed; Sharpe is not a utility.
- HAC p-values use a normal approximation and a Bartlett kernel.
- Winsorised hour means are a robustness column. They are not the decision statistic.
- Costs are assumptions, not the user's actual fee tier.
- A rising buy-and-hold result is the market, not evidence of a timing edge.
- Passing the gate would still be a historical result. It would not be permission to trade real money.

Schema: btc-research-summary/v1.
