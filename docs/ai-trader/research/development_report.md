# Strategy research report

Generated 2026-11-21T11:32:42Z. **Simulated results on historical candles. Not trading profits.**

Daily Binance spot candles. Costs per side: fee 0.25% + slippage 0.05%; stress case is double. Orders fill at the next bar's open. Long or flat only, no leverage.

Out-of-sample (OOS) = stitched walk-forward test folds; parameters for each fold were chosen only on earlier data. The final 20% holdout was **not** touched in this run.

- BTCUSDT: 3383 bars, 2017-08-17 to 2026-11-20, 0 missing, sha256 `14435e814d81`
- ETHUSDT: 3383 bars, 2017-08-17 to 2026-11-20, 0 missing, sha256 `d45e7ec68cff`
- SOLUSDT: 2293 bars, 2020-08-11 to 2026-11-20, 0 missing, sha256 `5cb1702d93b4`

## BTCUSDT

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom | REJECTED | +780.3% | +555.4% | +1081.5% | -61.9% | 1.17 | 49 | 31% | 4.19 | 59% | 80% | 100% | 0.68 | 0.18 | not scored | not scored |
| ema_cross | REJECTED | +1049.5% | +871.2% | +1260.0% | -59.1% | 1.30 | 28 | 43% | 4.93 | 56% | 80% | 100% | 0.78 | 0.08 | not scored | not scored |
| donchian | REJECTED | +210.5% | +170.4% | +256.5% | -60.3% | 0.76 | 23 | 39% | 3.39 | 48% | 60% | 100% | 0.32 | 0.56 | not scored | not scored |
| volume_breakout | REJECTED | +1197.7% | +1078.5% | +1328.6% | -37.8% | 1.51 | 16 | 69% | 12.60 | 41% | 80% | 100% | 0.89 | 0.01 | not scored | not scored |
| bollinger_reversion | REJECTED | -20.4% | -26.4% | -14.0% | -47.4% | 0.04 | 13 | 62% | 0.79 | 14% | 40% | 50% | 0.02 | 0.81 | not scored | not scored |
| rsi_reversion | REJECTED | -18.4% | -33.5% | +0.1% | -35.1% | -0.24 | 34 | 59% | 0.74 | 6% | 40% | 62% | 0.00 | 0.93 | not scored | not scored |
| vol_target_trend | REJECTED | +399.7% | +351.2% | +448.7% | -37.4% | 1.26 | 17 | 47% | 5.16 | 37% | 80% | 100% | 0.75 | 0.15 | not scored | not scored |
| trend_ensemble | REJECTED | +339.3% | +276.7% | +411.9% | -36.8% | 1.19 | 46 | 22% | 5.06 | 40% | 60% | 100% | 0.70 | 0.28 | not scored | not scored |
| regime_switch | REJECTED | +183.8% | +61.2% | +399.1% | -56.9% | 0.74 | 94 | 44% | 2.05 | 38% | 60% | 100% | 0.30 | 0.45 | not scored | not scored |
| buy_and_hold | benchmark | +969.6% | +937.9% | +1002.2% | -76.8% | 1.07 | 5 | 60% | 10.99 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **tsmom**: max_drawdown, deflated_sharpe, timing_edge
- **ema_cross**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **donchian**: oos_trades, max_drawdown, sharpe, deflated_sharpe, timing_edge
- **volume_breakout**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **bollinger_reversion**: oos_trades, net_positive, net_positive_2x_costs, profit_factor, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **rsi_reversion**: net_positive, net_positive_2x_costs, profit_factor, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **vol_target_trend**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **trend_ensemble**: max_drawdown, deflated_sharpe, timing_edge
- **regime_switch**: max_drawdown, sharpe, deflated_sharpe, timing_edge

## ETHUSDT

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom | REJECTED | +151.1% | +65.8% | +280.1% | -70.6% | 0.62 | 69 | 28% | 2.16 | 55% | 40% | 100% | 0.36 | 0.61 | not scored | not scored |
| ema_cross | REJECTED | +852.4% | +714.4% | +1013.4% | -63.3% | 1.05 | 26 | 42% | 4.42 | 56% | 60% | 100% | 0.71 | 0.19 | not scored | not scored |
| donchian | REJECTED | +807.1% | +680.4% | +954.0% | -49.1% | 1.06 | 25 | 48% | 4.61 | 44% | 60% | 100% | 0.73 | 0.11 | not scored | not scored |
| volume_breakout | REJECTED | +838.8% | +712.5% | +984.3% | -49.2% | 1.08 | 24 | 50% | 4.85 | 43% | 80% | 100% | 0.74 | 0.10 | not scored | not scored |
| bollinger_reversion | INSUFFICIENT_EVIDENCE | +222.0% | +195.9% | +250.2% | -24.3% | 1.02 | 14 | 93% | 17.03 | 9% | 100% | 88% | 0.71 | 0.01 | not scored | not scored |
| rsi_reversion | REJECTED | -32.1% | -38.3% | -25.2% | -44.5% | -0.23 | 16 | 50% | 0.50 | 3% | 20% | 25% | 0.01 | 0.83 | not scored | not scored |
| vol_target_trend | REJECTED | +439.7% | +386.3% | +493.5% | -50.1% | 1.06 | 20 | 45% | 3.79 | 39% | 60% | 100% | 0.72 | 0.24 | not scored | not scored |
| trend_ensemble | REJECTED | +306.9% | +257.3% | +364.7% | -30.8% | 1.13 | 32 | 25% | 4.69 | 31% | 60% | 100% | 0.78 | 0.28 | not scored | not scored |
| regime_switch | REJECTED | +335.6% | +126.0% | +738.4% | -51.7% | 0.81 | 109 | 57% | 1.90 | 41% | 60% | 75% | 0.51 | 0.21 | not scored | not scored |
| buy_and_hold | benchmark | +1097.9% | +1062.4% | +1134.5% | -79.4% | 1.02 | 5 | 60% | 12.25 | 100% | 60% | 100% | n/a | 1.00 | not scored | not scored |

Failed checks:

- **tsmom**: max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **ema_cross**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **donchian**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **volume_breakout**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **bollinger_reversion**: oos_trades, deflated_sharpe, timing_edge
- **rsi_reversion**: oos_trades, net_positive, net_positive_2x_costs, profit_factor, max_drawdown, sharpe, folds_positive, grid_positive, deflated_sharpe, timing_edge
- **vol_target_trend**: oos_trades, max_drawdown, deflated_sharpe, timing_edge
- **trend_ensemble**: max_drawdown, deflated_sharpe, timing_edge
- **regime_switch**: max_drawdown, deflated_sharpe, timing_edge

## SOLUSDT

| Strategy | Verdict | OOS net | 2x costs | Gross | Max DD | Sharpe | Trades | Win | PF | Exposure | Folds + | Grid + | DSR | Timing p | Holdout net | Holdout DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tsmom | REJECTED | +409.2% | +343.3% | +484.6% | -52.6% | 1.24 | 23 | 52% | 7.42 | 64% | 33% | 100% | 0.61 | 0.37 | not scored | not scored |
| ema_cross | REJECTED | +282.6% | +266.8% | +299.0% | -57.9% | 1.05 | 7 | 43% | 9.10 | 71% | 100% | 100% | 0.48 | 0.58 | not scored | not scored |
| donchian | REJECTED | +88.5% | +79.7% | +97.8% | -68.3% | 0.71 | 8 | 38% | 3.37 | 37% | 33% | 100% | 0.27 | 0.57 | not scored | not scored |
| volume_breakout | REJECTED | +88.5% | +79.7% | +97.8% | -68.3% | 0.71 | 8 | 38% | 3.37 | 37% | 33% | 100% | 0.27 | 0.57 | not scored | not scored |
| bollinger_reversion | REJECTED | -8.1% | -12.5% | -3.6% | -32.4% | 0.04 | 8 | 50% | 0.85 | 14% | 67% | 100% | 0.05 | 0.88 | not scored | not scored |
| rsi_reversion | INSUFFICIENT_EVIDENCE | +9.3% | +2.3% | +16.8% | -14.4% | 0.30 | 11 | 64% | 1.52 | 3% | 67% | 38% | 0.11 | 0.39 | not scored | not scored |
| vol_target_trend | INSUFFICIENT_EVIDENCE | +93.1% | +86.8% | +98.1% | -20.9% | 1.20 | 10 | 40% | 3.69 | 22% | 100% | 100% | 0.58 | 0.43 | not scored | not scored |
| trend_ensemble | INSUFFICIENT_EVIDENCE | +102.9% | +93.5% | +113.3% | -20.4% | 1.16 | 16 | 38% | 4.16 | 24% | 100% | 100% | 0.55 | 0.46 | not scored | not scored |
| regime_switch | REJECTED | +29.5% | -12.5% | +91.3% | -58.7% | 0.45 | 65 | 35% | 1.46 | 37% | 33% | 100% | 0.15 | 0.78 | not scored | not scored |
| buy_and_hold | benchmark | +790.6% | +774.7% | +806.8% | -60.0% | 1.40 | 3 | 100% | n/a | 100% | 100% | 100% | n/a | 0.97 | not scored | not scored |

Failed checks:

- **tsmom**: oos_trades, oos_years, max_drawdown, folds_positive, deflated_sharpe, timing_edge
- **ema_cross**: oos_trades, oos_years, max_drawdown, deflated_sharpe, timing_edge
- **donchian**: oos_trades, oos_years, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **volume_breakout**: oos_trades, oos_years, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
- **bollinger_reversion**: oos_trades, oos_years, net_positive, net_positive_2x_costs, profit_factor, max_drawdown, sharpe, deflated_sharpe, timing_edge
- **rsi_reversion**: oos_trades, oos_years, sharpe, grid_positive, deflated_sharpe, timing_edge
- **vol_target_trend**: oos_trades, oos_years, deflated_sharpe, timing_edge
- **trend_ensemble**: oos_trades, oos_years, deflated_sharpe, timing_edge
- **regime_switch**: oos_years, net_positive_2x_costs, max_drawdown, sharpe, folds_positive, deflated_sharpe, timing_edge
