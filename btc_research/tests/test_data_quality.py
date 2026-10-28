import unittest

from btc_research.data.binance import parse_kline_row
from btc_research.data.candles import Candle
from btc_research.data.validate import validate_candles
from btc_research.exceptions import DataIntegrityError
from support import HOUR, make_candles


class DataQualityTests(unittest.TestCase):
    def test_identical_duplicate_is_counted_and_not_doubled(self) -> None:
        candles = make_candles([100, 101, 102])
        dirty = [candles[0], candles[0], candles[1], candles[2]]
        clean, report = validate_candles(dirty, HOUR)
        self.assertEqual(len(clean), 3)
        self.assertEqual(report.n_duplicate_identical, 1)
        self.assertEqual(report.missing_bars, 0)

    def test_conflicting_duplicate_is_an_error(self) -> None:
        candles = make_candles([100, 101])
        clash = candles[0].replace(close=50, high=100, low=40)
        with self.assertRaises(DataIntegrityError):
            validate_candles([candles[0], clash], HOUR)

    def test_out_of_order_is_not_sorted_silently(self) -> None:
        candles = make_candles([100, 101, 102])
        with self.assertRaises(DataIntegrityError):
            validate_candles([candles[0], candles[2], candles[1]], HOUR)

    def test_gap_is_counted_and_not_filled(self) -> None:
        candles = make_candles([100, 101, 102, 103])
        gapped = [candles[0], candles[3]]
        clean, report = validate_candles(gapped, HOUR)
        self.assertEqual(len(clean), 2)
        self.assertEqual(report.n_gaps, 1)
        self.assertEqual(report.missing_bars, 2)
        self.assertEqual(clean[1].open_time_ms - clean[0].open_time_ms, 3 * HOUR)

    def test_malformed_ohlc_is_dropped_not_repaired(self) -> None:
        candles = make_candles([100, 101])
        bad = candles[1].replace(high=50, low=40, open=90, close=95)
        clean, report = validate_candles([candles[0], bad], HOUR)
        self.assertEqual(len(clean), 1)
        self.assertEqual(report.n_malformed, 1)

    def test_binance_row_parser(self) -> None:
        candle = parse_kline_row([1_577_836_800_000, "100", "110", "90", "105", "12.5", 1_577_840_399_999])
        self.assertIsInstance(candle, Candle)
        self.assertEqual(candle.close, 105.0)


if __name__ == "__main__":
    unittest.main()
