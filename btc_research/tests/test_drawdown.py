import unittest

from btc_research.backtest.engine import run_backtest
from btc_research.backtest.metrics import summarise
from btc_research.costs import CostModel, ExecutionConfig
from btc_research.stats import max_drawdown
from support import make_candles


class DrawdownTests(unittest.TestCase):
    def test_known_peak_to_trough(self) -> None:
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 90, 150])["max_drawdown"], 0.25)

    def test_losing_round_trip_reduces_equity_and_is_not_a_win(self) -> None:
        candles = make_candles(
            [120.0, 90.0, 150.0],
            opens=[100.0, 100.0, 100.0],
        )
        execution = ExecutionConfig(cost=CostModel(0, 0, 0), initial_equity=10_000.0)
        result = run_backtest(candles, [1.0, 1.0, 1.0], execution)
        stats = summarise(
            result, candles, start=0, end=3, periods_per_year=24 * 365.25, initial_equity=10_000.0
        )
        self.assertAlmostEqual(result.equity[0], 12_000.0, places=4)
        self.assertAlmostEqual(result.equity[1], 9_000.0, places=4)
        self.assertAlmostEqual(stats["max_drawdown"], 0.25, places=6)

        losing = make_candles([100.0, 90.0], opens=[100.0, 90.0])
        lost = run_backtest(losing, [1.0, 0.0], ExecutionConfig(cost=CostModel(0, 0, 0)))
        self.assertEqual(len(lost.round_trips), 1)
        self.assertLess(lost.round_trips[0].pnl, 0)
        self.assertLess(lost.equity[-1], 10_000.0)


if __name__ == "__main__":
    unittest.main()
