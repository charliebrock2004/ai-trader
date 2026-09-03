"""Prediction-market adapters. Every implementation here is paper."""

from ai_trader.markets.base import (
    BookLevel,
    Contract,
    ContractFill,
    MarketDataError,
    OrderBook,
    OrderResult,
    PredictionMarketAdapter,
)
from ai_trader.markets.fees import (
    PREMIUM_FEES,
    STANDARD_FEES,
    BinaryTradeFeeModel,
    FeeModel,
    ZeroFeeModel,
    break_even_edge,
    round_up_to_cent,
)
from ai_trader.markets.paper import PaperPredictionMarket

__all__ = [
    "BinaryTradeFeeModel",
    "BookLevel",
    "Contract",
    "ContractFill",
    "FeeModel",
    "MarketDataError",
    "OrderBook",
    "OrderResult",
    "PREMIUM_FEES",
    "PaperPredictionMarket",
    "PredictionMarketAdapter",
    "STANDARD_FEES",
    "ZeroFeeModel",
    "break_even_edge",
    "round_up_to_cent",
]
