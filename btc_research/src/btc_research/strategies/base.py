"""Strategy interface.

`targets(candles)[t]` is the desired spot exposure, in [0, 1], to be executed
at the OPEN of bar t.

The decision may use completed bars `candles[:t]` and the clock time
`candles[t].open_time_ms`. It must not use the open, high, low, close or
volume of bar t, and it must not use any later bar. The open price of bar t
is the fill, not a signal.

Long/flat only. No shorts, no leverage. These are research baselines.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from btc_research.data.candles import Candle


class Strategy(Protocol):
    name: str

    def targets(self, candles: Sequence[Candle]) -> list[float]:
        """One exposure per bar, decided at that bar's open from past closes."""


def assert_exposures(name: str, targets: Sequence[float], n: int) -> list[float]:
    if len(targets) != n:
        raise ValueError(f"{name} returned {len(targets)} targets for {n} bars")
    cleaned: list[float] = []
    for index, value in enumerate(targets):
        if value != value or value < -1e-12 or value > 1.0 + 1e-12:
            raise ValueError(f"{name} target[{index}]={value} is outside [0, 1]")
        cleaned.append(0.0 if value < 0 else (1.0 if value > 1 else float(value)))
    return cleaned
