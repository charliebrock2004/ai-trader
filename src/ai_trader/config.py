"""Configuration. Secrets stay in the environment; this module never logs them."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_trader.safety import ALPACA_PAPER_BASE_URL, assert_safe_to_run


def project_root() -> Path:
    """Workspace / repo root (parent of src/)."""
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    trading_mode: str = "simulate"

    xai_api_key: Optional[SecretStr] = None
    xai_model: str = "grok-4.3"
    xai_base_url: str = "https://api.x.ai/v1"

    alpaca_api_key: Optional[SecretStr] = None
    alpaca_secret_key: Optional[SecretStr] = None
    alpaca_base_url: str = ALPACA_PAPER_BASE_URL

    database_path: Path = Field(default=Path("data/ai_trader.db"))
    log_level: str = "INFO"
    log_dir: Path = Field(default=Path("logs"))

    kill_switch_engaged: bool = True
    grok_paper_analysis: bool = False
    xai_timeout: float = 8.0
    #: Hard cap on paper-analysis HTTP calls per UTC day. Exhaustion is HOLD
    #: or the deterministic filter — never a reason to widen risk.
    grok_daily_call_budget: int = 8
    #: Minimum seconds between Grok HTTP calls.
    grok_min_interval_seconds: int = 1800

    #: Opportunity-score threshold: a setup at or above this becomes a candidate
    #: and reaches the analyst. This is where the strategy's selectivity lives,
    #: as one number that can be tuned against recorded outcomes instead of by
    #: bolting on another filter. Raising it trades less; lowering it trades
    #: more. It can never bypass the guardian or the risk engine.
    strategy_score_threshold: float = 0.68

    # -- agent economics ---------------------------------------------------
    #: The experiment's opening stake, in the base accounting currency.
    starting_equity: float = 100.00
    base_currency: str = "GBP"
    #: Fraction of starting equity at which the agent is permanently shut down.
    terminal_threshold_pct: float = 0.40
    #: Known daily running cost, used for the runway estimate.
    hosting_cost_per_day: float = 0.0
    #: Optional BLS registration key. Lifts the anonymous request quota.
    bls_api_key: Optional[str] = None
    #: Required to call any mutating endpoint. Empty means mutations are refused.
    #: The Node frontend reads AI_TRADER_API_TOKEN, so Python has to read the
    #: same name or the two halves disagree about whether control is enabled.
    api_token: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("AI_TRADER_API_TOKEN", "API_TOKEN"),
    )

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080

    #: The persistent worker (``python -m ai_trader http``). PORT is what a
    #: managed host injects; binding anything else means the host reports no
    #: open ports and fails the deploy.
    worker_host: str = "0.0.0.0"
    worker_port: int = Field(default=8090, validation_alias=AliasChoices("PORT", "WORKER_PORT"))

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def _enforce_safety(self) -> "Settings":
        normalised = assert_safe_to_run(
            mode=self.trading_mode, alpaca_base_url=self.alpaca_base_url
        )
        self.trading_mode = normalised
        return self

    def resolve_database_path(self) -> Path:
        path = self.database_path
        if not path.is_absolute():
            path = project_root() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_log_dir(self) -> Path:
        path = self.log_dir
        if not path.is_absolute():
            path = project_root() / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve_kill_switch_path(self) -> Path:
        return self.resolve_database_path().parent / "KILL_SWITCH"

    def grok_configured(self) -> bool:
        return bool(self.xai_api_key and self.xai_api_key.get_secret_value().strip())

    def alpaca_configured(self) -> bool:
        key = self.alpaca_api_key.get_secret_value().strip() if self.alpaca_api_key else ""
        secret = (
            self.alpaca_secret_key.get_secret_value().strip()
            if self.alpaca_secret_key
            else ""
        )
        return bool(key and secret)

    def public_view(self) -> dict:
        """Safe for the dashboard and logs. No secrets."""
        return {
            "trading_mode": self.trading_mode,
            "xai_model": self.xai_model,
            "xai_base_url": self.xai_base_url,
            "xai_configured": self.grok_configured(),
            "grok_daily_call_budget": self.grok_daily_call_budget,
            "grok_min_interval_seconds": self.grok_min_interval_seconds,
            "strategy_score_threshold": self.strategy_score_threshold,
            "alpaca_base_url": self.alpaca_base_url,
            "alpaca_configured": self.alpaca_configured(),
            "database_path": str(self.resolve_database_path()),
            "log_level": self.log_level,
            "kill_switch_default": self.kill_switch_engaged,
            "grok_paper_analysis": self.grok_paper_analysis,
            "dashboard_host": self.dashboard_host,
            "dashboard_port": self.dashboard_port,
            "live_trading": False,
            "starting_equity": self.starting_equity,
            "base_currency": self.base_currency,
            "terminal_threshold_pct": self.terminal_threshold_pct,
            # Presence only. The value never leaves the server.
            "api_token_configured": bool(
                self.api_token and self.api_token.get_secret_value().strip()
            ),
            "bls_api_key_configured": bool(self.bls_api_key),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
