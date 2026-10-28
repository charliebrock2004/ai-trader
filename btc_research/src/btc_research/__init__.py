"""BTC spot research engine.

Historical research only. This package does not place orders, does not read
private exchange credentials, and does not talk to PeakSwiftBig's database.

Public entry points:
    run_study, load_candles_csv, CostModel, ExecutionConfig
"""

from btc_research.costs import CostModel, ExecutionConfig
from btc_research.data.candles import Candle, load_candles_csv, save_candles_csv
from btc_research.pipeline import StudyConfig, run_study

__all__ = [
    "Candle",
    "CostModel",
    "ExecutionConfig",
    "StudyConfig",
    "load_candles_csv",
    "run_study",
    "save_candles_csv",
]

__version__ = "0.1.0"
