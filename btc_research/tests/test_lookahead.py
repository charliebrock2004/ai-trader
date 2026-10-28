import unittest

from btc_research.backtest.engine import run_backtest
from btc_research.costs import CostModel, ExecutionConfig
from btc_research.strategies.baselines import MeanReversion, Momentum, TimeWindow, VolFilteredMomentum, default_strategies
from support import HOUR, make_candles


def _series(n: int = 80) -> list:
    closes = []
    price = 100.0
    for index in range(n):
        price *= 1.0 + (0.01 if index % 17 < 6 else -0.008)
        closes.append(price)
    return make_candles(closes)


class LookaheadTests(unittest.TestCase):
    def test_targets_ignore_the_current_bar_and_the_future(self) -> None:
        candles = _series()
        strategies = [
            *default_strategies(HOUR),
            VolFilteredMomentum(8, 8, 10, HOUR),
            MeanReversion(12, HOUR),
            Momentum(5, HOUR),
            TimeWindow(frozenset({0, 1, 2}), "hours"),
        ]
        for strategy in strategies:
            base = strategy.targets(candles)
            for index in (10, 25, 40):
                mutated = list(candles)
                mutated[index] = candles[index].replace(open=1.0, high=5.0, low=0.5, close=4.0)
                mutated[-1] = candles[-1].replace(close=candles[-1].close * 3, high=candles[-1].close * 3)
                got = strategy.targets(mutated)
                self.assertEqual(got[: index + 1], base[: index + 1], strategy.name)

    def test_prefix_matches_full_sample_targets(self) -> None:
        candles = _series()
        for strategy in default_strategies(HOUR):
            full = strategy.targets(candles)
            for length in (15, 30, 50):
                self.assertEqual(strategy.targets(candles[:length]), full[:length], strategy.name)

    def test_future_close_does_not_change_past_positions(self) -> None:
        candles = _series(40)
        targets = Momentum(5, HOUR).targets(candles)
        execution = ExecutionConfig(cost=CostModel(0, 0, 0))
        base = run_backtest(candles, targets, execution)
        mutated = list(candles)
        mutated[-1] = candles[-1].replace(close=1.0, high=2.0, low=0.5)
        # Keep the signal identical: the engine is given the original targets.
        later = run_backtest(mutated, targets, execution)
        self.assertEqual(base.position_qty[:-1], later.position_qty[:-1])
        self.assertEqual(base.equity[:-1], later.equity[:-1])


if __name__ == "__main__":
    unittest.main()
