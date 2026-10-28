import unittest

from btc_research.research.bayesian import estimate_positive_edge
from btc_research.stats import benjamini_hochberg, effective_sample_size


class BayesianTests(unittest.TestCase):
    def test_positive_sample_moves_above_half_and_is_idempotent(self) -> None:
        returns = [0.001] * 400 + [-0.0002] * 100
        first = estimate_positive_edge(returns, periods_per_year=24 * 365.25)
        second = estimate_positive_edge(returns, periods_per_year=24 * 365.25)
        self.assertEqual(first, second)
        self.assertGreater(first["p_mean_positive"], 0.5)
        self.assertFalse(hasattr(estimate_positive_edge, "update"))

    def test_zero_mean_stays_near_the_prior(self) -> None:
        returns = [0.01, -0.01] * 200
        result = estimate_positive_edge(returns, periods_per_year=24 * 365.25)
        self.assertAlmostEqual(result["p_mean_positive"], 0.5, places=2)

    def test_positive_autocorrelation_shrinks_the_effective_sample(self) -> None:
        self.assertLess(effective_sample_size(100, 0.5), 100)
        self.assertEqual(effective_sample_size(100, -0.4), 100.0)

    def test_benjamini_hochberg_known_values(self) -> None:
        adjusted = benjamini_hochberg([0.001, 0.01, 0.03, 0.2])
        self.assertLess(adjusted[0], 0.05)
        self.assertLess(adjusted[1], 0.05)
        self.assertLess(adjusted[2], 0.05)
        self.assertGreater(adjusted[3], 0.05)


if __name__ == "__main__":
    unittest.main()
