# Strategy research report, round 2

Generated 2026-11-21T13:35:28Z. **Simulated results on historical candles. Not trading profits.**

Binance spot candles. Costs per side: fee 0.25% + slippage 0.05%; stress case is double. Orders fill at the next bar's open. Long or flat only, no leverage. The final 20% holdout was **not** touched in this run, so no strategy can be marked as qualified here; `INSUFFICIENT_EVIDENCE` means it has not failed a development check yet.

The timing test is corrected for all 18 candidates tried across both rounds. For baskets, the benchmark and the timing test use an equal-weight hold of the same assets.

Verdicts: 3 INSUFFICIENT_EVIDENCE, 22 REJECTED (of 25).

## BTCUSDT daily

3383 bars, 2017-08-17 to 2026-11-20, sha256 `14435e814d81`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| brake_ema | REJECTED | +252.0% | +212.1% | +296.9% | -58.8% | 0.87 | 20 | 45% | 3.46 | 46% | 60% | 100% | 0.41 | 0.48 | not scored | not scored |
| brake_ensemble | REJECTED | +330.7% | +268.9% | +402.6% | -33.7% | 1.19 | 47 | 21% | 5.38 | 39% | 60% | 100% | 0.70 | 0.26 | not scored | not scored |
| low_vol_ensemble | INSUFFICIENT_EVIDENCE | +108.6% | +102.2% | +116.2% | -8.8% | 1.56 | 19 | 42% | 6.51 | 13% | 80% | 100% | 0.93 | 0.04 | not scored | not scored |
| buy_and_hold | benchmark | +969.6% | +937.9% | +1002.2% | -76.8% | 1.07 | 5 | 60% | 10.99 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **brake_ema**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **brake_ensemble**: max_drawdown, deflated_sharpe, timing_edge
- **low_vol_ensemble**: oos_trades, timing_edge

## ETHUSDT daily

3383 bars, 2017-08-17 to 2026-11-20, sha256 `d45e7ec68cff`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| brake_ema | REJECTED | +360.7% | +301.1% | +428.9% | -68.4% | 0.87 | 23 | 30% | 3.11 | 42% | 60% | 100% | 0.55 | 0.38 | not scored | not scored |
| brake_ensemble | REJECTED | +318.6% | +275.6% | +378.4% | -34.7% | 1.19 | 35 | 26% | 4.72 | 27% | 60% | 100% | 0.81 | 0.15 | not scored | not scored |
| low_vol_ensemble | REJECTED | +102.4% | +90.8% | +120.7% | -18.2% | 1.15 | 30 | 40% | 3.70 | 13% | 60% | 100% | 0.78 | 0.23 | not scored | not scored |
| buy_and_hold | benchmark | +1097.9% | +1062.4% | +1134.5% | -79.4% | 1.02 | 5 | 60% | 12.25 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **brake_ema**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **brake_ensemble**: max_drawdown, deflated_sharpe, timing_edge
- **low_vol_ensemble**: deflated_sharpe, timing_edge

## SOLUSDT daily

2293 bars, 2020-08-11 to 2026-11-20, sha256 `5cb1702d93b4`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| brake_ema | REJECTED | +116.1% | +95.1% | +139.4% | -55.2% | 0.83 | 17 | 41% | 2.69 | 31% | 33% | 100% | 0.35 | 0.46 | not scored | not scored |
| brake_ensemble | REJECTED | +99.4% | +90.3% | +109.8% | -25.0% | 1.15 | 16 | 31% | 3.81 | 23% | 100% | 100% | 0.57 | 0.41 | not scored | not scored |
| low_vol_ensemble | INSUFFICIENT_EVIDENCE | +55.8% | +51.6% | +60.1% | -12.4% | 1.22 | 22 | 36% | 4.13 | 13% | 100% | 100% | 0.61 | 0.36 | not scored | not scored |
| buy_and_hold | benchmark | +790.6% | +774.7% | +806.8% | -60.0% | 1.40 | 3 | 100% | n/a | 100% | 100% | 100% | n/a | 0.97 | not scored | not scored |

Failed checks:

- **brake_ema**: oos_trades, oos_years, max_drawdown, folds_positive, deflated_sharpe, timing_edge
- **brake_ensemble**: oos_trades, oos_years, max_drawdown, deflated_sharpe, timing_edge
- **low_vol_ensemble**: oos_trades, oos_years, deflated_sharpe, timing_edge

## BTCUSDT 4-hour

20284 bars, 2017-08-17 to 2026-11-21, sha256 `365d926ecf80`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom_4h | REJECTED | +301.9% | +83.8% | +777.5% | -65.3% | 0.84 | 130 | 21% | 2.67 | 59% | 40% | 100% | 0.89 | 0.56 | not scored | not scored |
| ema_cross_4h | REJECTED | +580.9% | +485.8% | +691.2% | -56.7% | 1.08 | 25 | 44% | 4.17 | 56% | 80% | 100% | 0.96 | 0.24 | not scored | not scored |
| donchian_4h | REJECTED | +368.7% | +310.5% | +434.9% | -43.7% | 0.97 | 22 | 55% | 3.30 | 47% | 60% | 100% | 0.93 | 0.30 | not scored | not scored |
| brake_ema_4h | REJECTED | +222.8% | +174.4% | +279.6% | -44.3% | 0.90 | 27 | 48% | 2.46 | 38% | 40% | 100% | 0.91 | 0.42 | not scored | not scored |
| buy_and_hold | benchmark | +926.7% | +896.3% | +958.0% | -77.2% | 1.06 | 5 | 60% | 10.44 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **tsmom_4h**: max_drawdown, folds_positive, deflated_sharpe, timing_edge
- **ema_cross_4h**: oos_trades, max_drawdown, timing_edge
- **donchian_4h**: oos_trades, max_drawdown, timing_edge
- **brake_ema_4h**: oos_trades, max_drawdown, folds_positive, timing_edge

## ETHUSDT 4-hour

20284 bars, 2017-08-17 to 2026-11-21, sha256 `18631a94edac`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom_4h | REJECTED | +172.2% | -7.9% | +702.4% | -75.2% | 0.64 | 180 | 27% | 1.94 | 55% | 40% | 100% | 0.67 | 0.68 | not scored | not scored |
| ema_cross_4h | REJECTED | +506.3% | +391.1% | +648.2% | -66.8% | 0.91 | 35 | 34% | 2.96 | 55% | 60% | 100% | 0.85 | 0.33 | not scored | not scored |
| donchian_4h | REJECTED | +1212.9% | +1002.6% | +1462.7% | -47.5% | 1.27 | 29 | 55% | 4.55 | 43% | 60% | 100% | 0.97 | 0.06 | not scored | not scored |
| brake_ema_4h | REJECTED | -40.7% | -50.5% | -29.0% | -61.8% | -0.10 | 30 | 37% | 0.82 | 32% | 40% | 75% | 0.11 | 0.98 | not scored | not scored |
| buy_and_hold | benchmark | +1041.9% | +1008.0% | +1076.7% | -81.2% | 1.01 | 5 | 60% | 12.98 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **tsmom_4h**: net_positive_2x_costs, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **ema_cross_4h**: max_drawdown, deflated_sharpe, timing_edge
- **donchian_4h**: oos_trades, max_drawdown, timing_edge
- **brake_ema_4h**: net_positive, net_positive_2x_costs, profit_factor, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge

## SOLUSDT 4-hour

13760 bars, 2020-08-11 to 2026-11-21, sha256 `3c1b8cccd73d`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom_4h | REJECTED | +665.4% | +494.4% | +885.0% | -58.2% | 1.43 | 42 | 33% | 6.83 | 67% | 67% | 100% | 0.89 | 0.17 | not scored | not scored |
| ema_cross_4h | REJECTED | +175.2% | +153.0% | +199.4% | -68.7% | 0.91 | 14 | 36% | 3.72 | 57% | 67% | 100% | 0.65 | 0.64 | not scored | not scored |
| donchian_4h | REJECTED | +184.1% | +170.7% | +198.1% | -60.1% | 0.97 | 8 | 62% | 4.64 | 40% | 67% | 100% | 0.68 | 0.39 | not scored | not scored |
| brake_ema_4h | REJECTED | +239.9% | +199.6% | +285.6% | -46.9% | 1.15 | 21 | 48% | 2.84 | 36% | 67% | 50% | 0.78 | 0.28 | not scored | not scored |
| buy_and_hold | benchmark | +778.9% | +763.2% | +794.9% | -66.3% | 1.39 | 3 | 100% | n/a | 100% | 100% | 100% | n/a | 0.98 | not scored | not scored |

Failed checks:

- **tsmom_4h**: oos_years, max_drawdown, deflated_sharpe, timing_edge
- **ema_cross_4h**: oos_trades, oos_years, max_drawdown, deflated_sharpe, timing_edge
- **donchian_4h**: oos_trades, oos_years, max_drawdown, deflated_sharpe, timing_edge
- **brake_ema_4h**: oos_trades, oos_years, max_drawdown, deflated_sharpe, timing_edge

## Basket BTCUSDT+ETHUSDT daily

3383 bars, 2017-08-17 to 2026-11-20, sha256 `c52d4502dc7a`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rotation | REJECTED | +761.4% | +523.9% | +1089.4% | -58.2% | 1.11 | 109 | 30% | 3.40 | 58% | 80% | 100% | 0.95 | 0.23 | not scored | not scored |
| rotation_vol | REJECTED | +158.6% | +122.8% | +206.2% | -27.1% | 1.18 | 101 | 34% | 2.57 | 23% | 80% | 100% | 0.97 | 0.33 | not scored | not scored |
| equal_weight | benchmark | +1238.2% | +1193.3% | +1284.6% | -76.9% | 1.10 | 10 | 60% | 11.44 | 100% | 80% | 100% | n/a | 0.57 | not scored | not scored |

Failed checks:

- **rotation**: max_drawdown, timing_edge
- **rotation_vol**: max_drawdown, timing_edge

## Basket BTCUSDT+ETHUSDT+SOLUSDT daily

2293 bars, 2020-08-11 to 2026-11-20, sha256 `d92432150042`

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rotation | REJECTED | +455.2% | +221.0% | +859.0% | -49.1% | 1.32 | 91 | 38% | 2.81 | 71% | 67% | 100% | 0.96 | 0.19 | not scored | not scored |
| rotation_vol | INSUFFICIENT_EVIDENCE | +88.1% | +60.1% | +119.2% | -23.7% | 1.31 | 104 | 34% | 2.26 | 31% | 100% | 100% | 0.95 | 0.48 | not scored | not scored |
| equal_weight | benchmark | +422.7% | +412.8% | +432.8% | -48.7% | 1.38 | 9 | 89% | 35.29 | 99% | 100% | 100% | n/a | 0.67 | not scored | not scored |

Failed checks:

- **rotation**: oos_years, max_drawdown, timing_edge
- **rotation_vol**: oos_years, timing_edge
