"""Named test markets. Each maps a ticker to a path-shape."""

from __future__ import annotations

from typing import TypedDict

from ai_trader.exceptions import InvalidMarketDataError


class ScenarioSpec(TypedDict):
    drift: float
    vol: float
    mean_reversion: float
    shock_at: float
    shock_move: float
    volume_base: float
    description: str


SCENARIOS: dict[str, ScenarioSpec] = {
    "uptrend": {
        "drift": 0.00115,
        "vol": 0.0055,
        "mean_reversion": 0.0,
        "shock_at": -1.0,
        "shock_move": 0.0,
        "volume_base": 1_100_000,
        "description": "Steady bid. Closes generally rise.",
    },
    "downtrend": {
        "drift": -0.00115,
        "vol": 0.0055,
        "mean_reversion": 0.0,
        "shock_at": -1.0,
        "shock_move": 0.0,
        "volume_base": 1_100_000,
        "description": "Steady offer. Closes generally fall.",
    },
    "sideways": {
        "drift": 0.0,
        "vol": 0.0032,
        "mean_reversion": 0.22,
        "shock_at": -1.0,
        "shock_move": 0.0,
        "volume_base": 750_000,
        "description": "Mean-reverting range. No trend.",
    },
    "high_volatility": {
        "drift": 0.00015,
        "vol": 0.018,
        "mean_reversion": 0.0,
        "shock_at": -1.0,
        "shock_move": 0.0,
        "volume_base": 2_400_000,
        "description": "Wide bars, noisy path, heavy volume.",
    },
    "shock": {
        "drift": 0.00012,
        "vol": 0.004,
        "mean_reversion": 0.04,
        "shock_at": 0.62,
        "shock_move": -0.085,
        "volume_base": 1_350_000,
        "description": "Quiet tape, then a sudden gap lower.",
    },
}

SYMBOL_SCENARIOS: dict[str, str] = {
    "SIM-UP": "uptrend",
    "SIM-DOWN": "downtrend",
    "SIM-FLAT": "sideways",
    "SIM-VOL": "high_volatility",
    "SIM-SHOCK": "shock",
}

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "SIM-UP",
    "SIM-DOWN",
    "SIM-FLAT",
    "SIM-VOL",
    "SIM-SHOCK",
)

DEFAULT_SCENARIO = "uptrend"


def resolve_scenario(symbol: str, override: str | None = None) -> str:
    if override:
        key = override.strip().lower()
        if key not in SCENARIOS:
            raise InvalidMarketDataError(f"Unknown scenario '{override}'.")
        return key
    mapped = SYMBOL_SCENARIOS.get(symbol.upper())
    if mapped:
        return mapped
    return DEFAULT_SCENARIO


def scenario_spec(name: str) -> ScenarioSpec:
    key = name.strip().lower()
    if key not in SCENARIOS:
        allowed = ", ".join(SCENARIOS)
        raise InvalidMarketDataError(f"Unknown scenario '{name}'. Allowed: {allowed}.")
    return SCENARIOS[key]
