"""Continuous paper-trading session. No broker. No live trading."""

from ai_trader.session.config import PaperSessionConfig
from ai_trader.session.runner import PaperSession

__all__ = ["PaperSession", "PaperSessionConfig"]
