import unittest
from datetime import datetime, timezone

from btc_research.costs import CostModel, ExecutionConfig
from btc_research.pipeline import evaluate_candles
from support import HOUR, make_candles


def _market(n: int = 220) -> list:
    price = 100.0
    closes = []
    for index in range(n):
        drift = 0.002 if (index // 12) % 2 == 0 else -0.0015
        price *= 1.0 + drift
        closes.append(price)
    return make_candles(closes)


class ReproducibleTests(unittest.TestCase):
    def test_same_candles_same_summary(self) -> None:
        candles = _market()
        execution = ExecutionConfig(cost=CostModel(10, 1, 0.5), initial_equity=10_000)
        stamp = datetime(2026, 10, 28, tzinfo=timezone.utc)
        kwargs = dict(
            execution=execution,
            interval_ms=HOUR,
            train_bars=80,
            test_bars=40,
            step_bars=40,
            meta={"symbol": "BTCUSDT", "interval": "1h", "source": "fixture", "host": "fixture"},
            generated_at=stamp,
        )
        first = evaluate_candles(candles, **kwargs)
        second = evaluate_candles(candles, **kwargs)
        self.assertEqual(first, second)
        self.assertIn(first["verdict"]["code"], {"insufficient_evidence", "insufficient_sample", "oos_gate_passed"})
        self.assertEqual(first["performance_classes"]["paper_trading"], "not_run")
        self.assertEqual(first["performance_classes"]["live_trading"], "not_run")
        self.assertGreaterEqual(first["ranking"][0]["n_folds"], 1)


if __name__ == "__main__":
    unittest.main()
