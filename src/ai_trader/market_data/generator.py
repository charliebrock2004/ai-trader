"""Deterministic OHLCV generator. No I/O. Same seed → same series."""

from __future__ import annotations

import math
from datetime import datetime

from ai_trader.market_data.scenarios import ScenarioSpec, resolve_scenario, scenario_spec
from ai_trader.market_data.timeframes import SERIES_START, bar_time, iso_utc
from ai_trader.market_data.validation import validate_series, validate_symbol
from ai_trader.types import Candle, CandleSeries

MOD = 2147483647
MUL = 48271
DEFAULT_SEED = 42
DEFAULT_LIMIT = 48


def round2(value: float) -> float:
    """Half-up to 2 d.p. Matches JS Math.round for positive prices."""
    return math.floor(value * 100.0 + 0.5) / 100.0


def round_volume(value: float) -> float:
    return float(int(math.floor(max(0.0, value) + 0.5)))


class LCG:
    """Park–Miller LCG. Portable between Python and JavaScript."""

    def __init__(self, seed: int) -> None:
        state = int(seed) % MOD
        if state <= 0:
            state += MOD - 1
        self.state = state

    def next(self) -> float:
        self.state = (self.state * MUL) % MOD
        return (self.state - 1) / (MOD - 1)

    def gauss(self) -> float:
        u = self.next()
        v = self.next()
        if u < 1e-12:
            u = 1e-12
        return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)


def mix_seed(base: int, symbol: str) -> int:
    h = int(base)
    for char in symbol.upper():
        h = (h * 33 + ord(char)) % MOD
    return h or 1


def start_price(symbol: str) -> float:
    h = 0
    for char in symbol.upper():
        h = (h * 33 + ord(char)) % 100000
    return round2(48.0 + (h % 320) + (h % 90) / 100.0)


def generate_series(
    symbol: str,
    *,
    timeframe: str = "5m",
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
    scenario: str | None = None,
    start: datetime = SERIES_START,
    source: str = "simulated",
) -> CandleSeries:
    ticker = validate_symbol(symbol)
    name = resolve_scenario(ticker, scenario)
    spec = scenario_spec(name)
    count = int(limit)
    if count < 2:
        count = 2
    from ai_trader.market_data.validation import MAX_BARS

    if count > MAX_BARS:
        count = MAX_BARS

    rng = LCG(mix_seed(seed, ticker))
    price = start_price(ticker)
    origin = price
    candles: list[Candle] = []

    for index in range(count):
        candle = _next_candle(
            rng,
            spec,
            index=index,
            count=count,
            prev_close=price,
            origin=origin,
            timestamp=iso_utc(bar_time(index, timeframe=timeframe, start=start)),
        )
        candles.append(candle)
        price = candle.close

    series = CandleSeries(
        symbol=ticker,
        timeframe=timeframe,
        scenario=name,
        seed=seed,
        candles=tuple(candles),
        source=source,
    )
    return validate_series(series)


def _next_candle(
    rng: LCG,
    spec: ScenarioSpec,
    *,
    index: int,
    count: int,
    prev_close: float,
    origin: float,
    timestamp: str,
) -> Candle:
    shock_index = int(count * spec["shock_at"]) if spec["shock_at"] >= 0 else -1
    deviation = (prev_close - origin) / origin
    ret = spec["drift"] + spec["vol"] * rng.gauss() - spec["mean_reversion"] * deviation
    if index == shock_index:
        ret += spec["shock_move"]

    close = max(0.5, prev_close * (1.0 + ret))
    gap = 0.12 * spec["vol"] * rng.gauss()
    open_ = max(0.5, prev_close * (1.0 + gap))
    wing = abs(close - open_) + spec["vol"] * prev_close * (0.35 + 0.8 * rng.next())
    high = max(open_, close) + wing * 0.55
    low = min(open_, close) - wing * 0.45
    low = max(0.25, low)
    if high < max(open_, close, low):
        high = max(open_, close, low)
    if low > min(open_, close, high):
        low = min(open_, close, high)

    volume = spec["volume_base"] * (0.65 + 0.7 * rng.next()) * (1.0 + 10.0 * abs(ret))
    if index == shock_index:
        volume *= 3.4

    return Candle(
        timestamp=timestamp,
        open=round2(open_),
        high=round2(high),
        low=round2(low),
        close=round2(close),
        volume=round_volume(volume),
    )
