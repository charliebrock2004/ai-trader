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
