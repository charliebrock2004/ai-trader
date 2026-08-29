"""Domain exceptions for AI-Trader."""


class AITraderError(Exception):
    """Base error."""


class LiveTradingBlockedError(AITraderError):
    """Raised when configuration or code would allow live trading."""


class KillSwitchEngagedError(AITraderError):
    """Raised when the kill switch is engaged."""


class OrderPlacementDisabledError(AITraderError):
    """Raised on any attempt to place an order during the foundation build."""


class BrokerNotEnabledError(AITraderError):
    """Raised when a broker adapter is not yet connected."""


class RiskRejectedError(AITraderError):
    """Raised when the risk engine rejects a proposed action."""


class FoundationModeError(AITraderError):
    """Raised for capabilities that exist as interfaces only."""


class InvalidMarketDataError(AITraderError):
    """Raised when a candle or series fails validation."""


class HistoricalDataNotConfiguredError(AITraderError):
    """Raised when a benchmark period asks for historical data that is not wired."""


class MarketDataUnavailableError(AITraderError):
    """Public feed down, timed out, or unusable. Session must HOLD/STOP."""

    def __init__(self, message: str, *, failure: str = "unavailable") -> None:
        super().__init__(message)
        self.failure = failure


class StaleMarketDataError(MarketDataUnavailableError):
    """Last completed candle is too old to trade on."""

    def __init__(self, message: str) -> None:
        super().__init__(message, failure="stale")


class AlpacaPaperUnavailableError(AITraderError):
    """Alpaca paper API down, unauthorized, or unusable. No trade is sent."""

    def __init__(self, message: str, *, failure: str = "unavailable") -> None:
        super().__init__(message)
        self.failure = failure
