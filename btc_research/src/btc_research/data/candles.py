"""Candle type and CSV cache format.

CSV columns, in order:
    open_time_ms,open,high,low,close,volume,close_time_ms

Times are UTC milliseconds. Prices are quote currency per 1 BTC.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


CSV_FIELDS = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
)

INTERVAL_MS = {
    "1h": 3_600_000,
    "1d": 86_400_000,
}


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int

    def replace(self, **changes: float | int) -> "Candle":
        data = {
            "open_time_ms": self.open_time_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "close_time_ms": self.close_time_ms,
        }
        data.update(changes)
        return Candle(**data)  # type: ignore[arg-type]


def save_candles_csv(path: Path | str, candles: list[Candle]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for candle in candles:
            writer.writerow(
                {
                    "open_time_ms": candle.open_time_ms,
                    "open": repr(candle.open),
                    "high": repr(candle.high),
                    "low": repr(candle.low),
                    "close": repr(candle.close),
                    "volume": repr(candle.volume),
                    "close_time_ms": candle.close_time_ms,
                }
            )
    temporary.replace(destination)


def load_candles_csv(path: Path | str) -> list[Candle]:
    """Load a cache file. Does not validate; call validate_candles next."""
    source = Path(path)
    candles: list[Candle] = []
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in CSV_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{source} is missing columns: {missing}")
        for line_number, row in enumerate(reader, start=2):
            try:
                candles.append(
                    Candle(
                        open_time_ms=int(row["open_time_ms"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        close_time_ms=int(row["close_time_ms"]),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{source}:{line_number} is not a candle") from exc
    return candles
