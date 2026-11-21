"""Historical candle store for research.

Source: Binance's public market-data mirror (``data-api.binance.vision``), GET
only, no key. Candles are cached as CSV under ``data/research/`` with a
SHA-256 so a report can name exactly the bytes it was computed from.
Only completed bars are kept.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
INTERVAL_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400}
DEFAULT_CACHE = Path("data/research")
PAGE = 1000


class ResearchDataError(RuntimeError):
    """The candle history cannot be trusted. Research must stop, not guess."""


@dataclass(frozen=True)
class Series:
    symbol: str
    interval: str
    ts: tuple[int, ...]  # bar open time, epoch seconds, UTC
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def bars_per_year(self) -> float:
        return 365.25 * 86400 / INTERVAL_SECONDS[self.interval]

    def prefix(self, n: int) -> "Series":
        return Series(self.symbol, self.interval, self.ts[:n], self.open[:n], self.high[:n],
                      self.low[:n], self.close[:n], self.volume[:n])

    def sha256(self) -> str:
        h = hashlib.sha256()
        for row in zip(self.ts, self.open, self.high, self.low, self.close, self.volume):
            h.update(repr(row).encode())
        return h.hexdigest()


def validate(series: Series) -> dict:
    """Refuse corrupt history; report (not fill) gaps."""
    step = INTERVAL_SECONDS[series.interval]
    if len(series) < 2:
        raise ResearchDataError(f"{series.symbol}: fewer than two bars")
    gaps = missing = 0
    for i in range(len(series)):
        o, h, l, c = series.open[i], series.high[i], series.low[i], series.close[i]
        if not (l > 0 and h >= max(o, c) and l <= min(o, c)):
            raise ResearchDataError(f"{series.symbol}: impossible OHLC at {series.ts[i]}")
        if i:
            d = series.ts[i] - series.ts[i - 1]
            if d <= 0 or d % step:
                raise ResearchDataError(f"{series.symbol}: duplicate or misaligned bar at {series.ts[i]}")
            if d > step:
                gaps += 1
                missing += d // step - 1
    return {"bars": len(series), "gaps": gaps, "missing_bars": missing,
            "first": series.ts[0], "last": series.ts[-1]}


def _http_json(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Trader-Research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read())


def fetch(symbol: str, interval: str, *, start: int = 0, now: Optional[int] = None,
          get: Callable[[str], list] = _http_json, pause: float = 0.15) -> Series:
    """Download every completed bar from ``start`` (epoch seconds)."""
    step = INTERVAL_SECONDS[interval]
    now = int(time.time()) if now is None else now
    rows: list[list] = []
    cursor = start * 1000
    while True:
        page = get(f"{BINANCE_KLINES}?symbol={symbol}&interval={interval}&startTime={cursor}&limit={PAGE}")
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + step * 1000
        if len(page) < PAGE:
            break
        time.sleep(pause)
    rows = [r for r in rows if int(r[0]) // 1000 + step <= now]  # completed bars only
    return _from_rows(symbol, interval, [(int(r[0]) // 1000, *map(float, r[1:6])) for r in rows])


def _from_rows(symbol: str, interval: str, rows: list[tuple]) -> Series:
    cols = list(zip(*rows)) if rows else [(), (), (), (), (), ()]
    return Series(symbol, interval, tuple(int(t) for t in cols[0]), *(tuple(c) for c in cols[1:6]))


def save(series: Series, cache: Path = DEFAULT_CACHE) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{series.symbol}_{series.interval}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        w.writerows(zip(series.ts, series.open, series.high, series.low, series.close, series.volume))
    return path


def load(symbol: str, interval: str, cache: Path = DEFAULT_CACHE) -> Series:
    path = cache / f"{symbol}_{interval}.csv"
    if not path.exists():
        raise ResearchDataError(f"no cached history at {path}; run `ai-trader research fetch`")
    with path.open() as f:
        r = csv.reader(f)
        next(r)
        rows = [(int(a), float(b), float(c), float(d), float(e), float(g)) for a, b, c, d, e, g in r]
    return _from_rows(symbol, interval, rows)


def synthetic(n: int = 1500, *, seed: int = 7, drift: float = 0.0004, vol: float = 0.03,
              symbol: str = "SYN", interval: str = "1d") -> Series:
    """Seeded random walk for tests. Never used in a report."""
    import random

    rng = random.Random(seed)
    step = INTERVAL_SECONDS[interval]
    rows, price = [], 100.0
    for i in range(n):
        o = price
        c = o * (1 + rng.gauss(drift, vol))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 3)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 3)))
        rows.append((1_500_000_000 + i * step, o, hi, lo, c, 1000.0))
        price = c
    return _from_rows(symbol, interval, rows)
