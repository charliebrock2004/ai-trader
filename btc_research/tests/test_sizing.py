import unittest

from btc_research.sizing.kelly import fractional_kelly


class SizingTests(unittest.TestCase):
    def test_non_positive_edge_is_zero(self) -> None:
        losing = fractional_kelly(mean_return=-0.01, variance=0.02)
        flat = fractional_kelly(mean_return=0.0, variance=0.02)
        trade = fractional_kelly(win_probability=0.4, avg_win=1.0, avg_loss=-1.0)
        self.assertEqual(losing.fraction, 0.0)
        self.assertEqual(flat.fraction, 0.0)
        self.assertEqual(trade.fraction, 0.0)

    def test_quarter_kelly_and_hard_cap(self) -> None:
        # mean/variance = 0.01/0.01 = 1; quarter Kelly = 0.25; cap 0.5 leaves 0.25.
        decision = fractional_kelly(mean_return=0.01, variance=0.01, kelly_fraction=0.25, max_exposure=0.5)
        self.assertAlmostEqual(decision.raw_kelly, 1.0)
        self.assertAlmostEqual(decision.fraction, 0.25)

        huge = fractional_kelly(mean_return=0.05, variance=0.01, kelly_fraction=1.0, max_exposure=0.2)
        self.assertAlmostEqual(huge.raw_kelly, 5.0)
        self.assertAlmostEqual(huge.fraction, 0.2)
        self.assertLessEqual(huge.fraction, 1.0)

    def test_trade_formula_and_the_conservative_of_two_views(self) -> None:
        # f* = 0.6 - 0.4/1 = 0.2; quarter = 0.05.
        decision = fractional_kelly(win_probability=0.6, avg_win=1.0, avg_loss=-1.0, max_exposure=1.0)
        self.assertAlmostEqual(decision.raw_kelly, 0.2, places=8)
        self.assertAlmostEqual(decision.fraction, 0.05, places=8)

        both = fractional_kelly(
            mean_return=0.02,
            variance=0.01,
            win_probability=0.55,
            avg_win=1.0,
            avg_loss=-1.0,
            kelly_fraction=1.0,
            max_exposure=1.0,
        )
        # return-space raw = 2, trade raw = 0.1, conservative raw is 0.1
        self.assertAlmostEqual(both.raw_kelly, 0.1, places=8)

    def test_leverage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fractional_kelly(mean_return=0.01, variance=0.01, max_exposure=1.5)


if __name__ == "__main__":
    unittest.main()
