import unittest

from btc_research.backtest.engine import run_backtest
from btc_research.costs import CostModel, ExecutionConfig
from support import make_candles


class FeeTests(unittest.TestCase):
    def test_round_trip_fees_match_the_closed_form(self) -> None:
        candles = make_candles([100.0, 110.0], opens=[100.0, 110.0])
        execution = ExecutionConfig(
            cost=CostModel(fee_bps=10, slippage_bps=0, half_spread_bps=0),
            initial_equity=10_000.0,
        )
        targets = [1.0, 0.0]
        result = run_backtest(candles, targets, execution)
        buy_factor = 1.001
        sell_factor = 0.999
        qty = 10_000.0 / (100.0 * buy_factor)
        expected_cash = qty * 110.0 * sell_factor
        self.assertAlmostEqual(result.final_cash, expected_cash, places=6)
        self.assertAlmostEqual(result.final_qty, 0.0, places=8)
        self.assertEqual(len(result.round_trips), 1)
        self.assertAlmostEqual(result.round_trips[0].pnl, expected_cash - 10_000.0, places=6)
        self.assertGreater(result.fills[0].fee, 0)
        self.assertGreater(result.fills[1].fee, 0)
        self.assertLess(result.final_cash, 11_000.0)
        self.assertGreater(result.final_cash, 0)

    def test_zero_cost_buy_uses_the_whole_cash_balance(self) -> None:
        candles = make_candles([100.0, 80.0], opens=[100.0, 80.0])
        execution = ExecutionConfig(cost=CostModel(0, 0, 0), initial_equity=10_000.0)
        result = run_backtest(candles, [1.0, 1.0], execution)
        self.assertAlmostEqual(result.position_qty[0], 100.0, places=8)
        self.assertAlmostEqual(result.equity[0], 10_000.0, places=6)
        self.assertAlmostEqual(result.equity[1], 8_000.0, places=6)
        self.assertGreaterEqual(min(result.equity), 0)


if __name__ == "__main__":
    unittest.main()
