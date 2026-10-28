import unittest

from btc_research.backtest.walkforward import fold_bounds
from btc_research.strategies.baselines import TimeWindow
from support import HOUR, make_candles


class WalkForwardTests(unittest.TestCase):
    def test_test_windows_do_not_overlap_and_follow_training(self) -> None:
        folds = fold_bounds(300, train_bars=100, test_bars=50, step_bars=50)
        self.assertGreaterEqual(len(folds), 3)
        previous_end = 0
        for train_start, test_start, test_end in folds:
            self.assertLess(train_start, test_start)
            self.assertLess(test_start, test_end)
            self.assertGreaterEqual(test_start, previous_end)
            self.assertEqual(test_start - train_start, 100)
            previous_end = test_end

    def test_time_window_uses_the_bar_clock_not_the_close(self) -> None:
        candles = make_candles([100 + index for index in range(48)])
        strategy = TimeWindow(frozenset({0}), "midnight")
        targets = strategy.targets(candles)
        self.assertEqual(targets[0], 1.0)
        self.assertEqual(targets[1], 0.0)
        self.assertEqual(sum(targets), 2)


if __name__ == "__main__":
    unittest.main()
