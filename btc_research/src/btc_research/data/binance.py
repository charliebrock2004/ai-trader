"""Download Binance spot klines from the public data API.

No API key is used. The client only calls public market-data endpoints.

`api.binance.com` returns HTTP 451 from some regions. The default host is
`data-api.binance.vision`, which serves the same public spot klines. Monthly
zip files on `data.binance.vision` are the fallback if the REST API fails.
This module never substitutes a different venue's prices for missing bars.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

from btc_research.data.candles import Candle, load_candles_csv, save_candles_csv
from btc_research.data.validate import validate_candles
from btc_research.exceptions import DataSourceError

DEFAULT_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
VISION_ZIP = "https://data.binance.vision/data/spot/monthly/klines"
USER_AGENT = "btc-research/0.1 (historical research; public market data only)"


def _request(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_json(url: str, timeout: float, retries: int, pause_s: float) -> object:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            payload = _request(url, timeout)
            return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401, 403, 404, 418, 451):
                break
            time.sleep(pause_s * (2**attempt))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            time.sleep(pause_s * (2**attempt))
    raise DataSourceError(f"request failed for {url}: {last_error}")


def parse_kline_row(row: list[object]) -> Candle:
    """Parse one Binance kline array. Raises ValueError if the row is short."""
    if len(row) < 7:
        raise ValueError("kline row has fewer than 7 fields")
    return Candle(
        open_time_ms=int(row[0]),  # type: ignore[arg-type]
        open=float(row[1]),  # type: ignore[arg-type]
        high=float(row[2]),  # type: ignore[arg-type]
        low=float(row[3]),  # type: ignore[arg-type]
        close=float(row[4]),  # type: ignore[arg-type]
        volume=float(row[5]),  # type: ignore[arg-type]
        close_time_ms=int(row[6]),  # type: ignore[arg-type]
    )


def _drop_incomplete(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Drop a still-forming bar. A bar is closed only once its close time is past."""
    return [candle for candle in candles if candle.close_time_ms < now_ms]


def fetch_klines_rest(
    *,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    hosts: tuple[str, ...] = DEFAULT_HOSTS,
    pause_s: float = 0.15,
    timeout: float = 30.0,
    retries: int = 5,
    now_ms: int | None = None,
    progress=None,
) -> tuple[list[Candle], str]:
    """Page through public klines. Returns candles and the host that served them."""
    if start_ms >= end_ms:
        raise ValueError("start_ms must be before end_ms")
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    errors: list[str] = []
    for host in hosts:
        try:
            candles = _fetch_from_host(
                host=host,
                symbol=symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
                interval_ms=interval_ms,
                pause_s=pause_s,
                timeout=timeout,
                retries=retries,
                now_ms=clock,
                progress=progress,
            )
            return candles, host
        except DataSourceError as exc:
            errors.append(f"{host}: {exc}")
    raise DataSourceError("all REST hosts failed: " + " | ".join(errors))


def _fetch_from_host(
    *,
    host: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
    pause_s: float,
    timeout: float,
    retries: int,
    now_ms: int,
    progress,
) -> list[Candle]:
    cursor = start_ms
    collected: list[Candle] = []
    pages = 0
    while cursor < end_ms:
        url = (
            f"{host}/api/v3/klines?symbol={symbol}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        payload = _get_json(url, timeout=timeout, retries=retries, pause_s=pause_s)
        if not isinstance(payload, list):
            raise DataSourceError(f"unexpected kline payload from {host}")
        if not payload:
            break
        page: list[Candle] = []
        for row in payload:
            if not isinstance(row, list):
                raise DataSourceError("kline row was not a list")
            page.append(parse_kline_row(row))
        page = _drop_incomplete(page, now_ms)
        page = [candle for candle in page if start_ms <= candle.open_time_ms <= end_ms]
        if not page:
            break
        if collected and page[-1].open_time_ms <= collected[-1].open_time_ms:
            break
        if collected:
            page = [candle for candle in page if candle.open_time_ms > collected[-1].open_time_ms]
        collected.extend(page)
        pages += 1
        if progress is not None:
            progress(pages, len(collected), page[-1].open_time_ms)
        next_cursor = page[-1].open_time_ms + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(payload) < 1000:
            break
        time.sleep(pause_s)
    return collected


def _month_iter(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    from datetime import datetime, timezone

    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    year, month = start.year, start.month
    pairs: list[tuple[int, int]] = []
    while (year, month) <= (end.year, end.month):
        pairs.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return pairs


def fetch_klines_monthly_zip(
    *,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    timeout: float = 60.0,
    retries: int = 3,
    pause_s: float = 0.2,
    now_ms: int | None = None,
) -> list[Candle]:
    """Fallback: Binance public monthly kline zip files. Missing months are skipped only if the REST path is not available and the month is the current (unpublished) month; a missing historical month raises."""
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    from datetime import datetime, timezone

    current = datetime.fromtimestamp(clock / 1000, tz=timezone.utc)
    candles: list[Candle] = []
    for year, month in _month_iter(start_ms, end_ms):
        name = f"{symbol}-{interval}-{year}-{month:02d}.zip"
        url = f"{VISION_ZIP}/{symbol}/{interval}/{name}"
        try:
            blob = _request_bytes_retry(url, timeout=timeout, retries=retries, pause_s=pause_s)
        except DataSourceError as exc:
            if year == current.year and month == current.month:
                continue
            message = str(exc)
            if "404" in message and not (year == current.year and month >= current.month):
                raise
            if "404" in message:
                continue
            raise
        with zipfile.ZipFile(BytesIO(blob)) as archive:
            csv_names = [info.filename for info in archive.infolist() if info.filename.endswith(".csv")]
            if not csv_names:
                raise DataSourceError(f"{name} contained no csv")
            text = archive.read(csv_names[0]).decode("utf-8")
        for line in text.splitlines():
            if not line or line.startswith("open_time"):
                continue
            parts = line.split(",")
            candle = parse_kline_row(parts[:7])
            if start_ms <= candle.open_time_ms <= end_ms and candle.close_time_ms < clock:
                candles.append(candle)
        time.sleep(pause_s)
    candles.sort(key=lambda candle: candle.open_time_ms)
    return candles


def _request_bytes_retry(url: str, timeout: float, retries: int, pause_s: float) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return _request(url, timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401, 403, 404, 451):
                break
            time.sleep(pause_s * (2**attempt))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(pause_s * (2**attempt))
    raise DataSourceError(f"download failed for {url}: {last_error}")


def load_or_download(
    *,
    cache_path: Path | str,
    symbol: str,
    interval: str,
    interval_ms: int,
    start_ms: int,
    end_ms: int,
    progress=None,
) -> tuple[list[Candle], dict[str, object]]:
    """Read the local cache when it already covers the range. Otherwise download.

    The cache is the REST result when that works. Zip files are used only if
    every REST host fails. The two sources are never silently mixed inside one
    file, because a partial mix would be hard to audit.
    """
    path = Path(cache_path)
    meta_path = path.with_suffix(".meta.json")
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        cached = load_candles_csv(path)
        candles, report = validate_candles(cached, interval_ms)
        covers = (
            meta.get("symbol") == symbol
            and meta.get("interval") == interval
            and candles
            and candles[0].open_time_ms <= start_ms + interval_ms
            and candles[-1].open_time_ms >= end_ms - 2 * interval_ms
        )
        if covers:
            window = [c for c in candles if start_ms <= c.open_time_ms <= end_ms]
            window, report = validate_candles(window, interval_ms)
            meta = dict(meta)
            meta["cache_hit"] = True
            meta["validation"] = report.to_dict()
            return window, meta

    source = "rest"
    host = ""
    try:
        downloaded, host = fetch_klines_rest(
            symbol=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            interval_ms=interval_ms,
            progress=progress,
        )
    except DataSourceError as rest_error:
        try:
            downloaded = fetch_klines_monthly_zip(
                symbol=symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            source = "monthly-zip"
            host = VISION_ZIP
        except DataSourceError as zip_error:
            raise DataSourceError(f"REST failed ({rest_error}); zip fallback failed ({zip_error})") from zip_error

    candles, report = validate_candles(downloaded, interval_ms)
    if len(candles) < 100:
        raise DataSourceError(f"download produced only {len(candles)} valid candles")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_candles_csv(path, candles)
    meta = {
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "host": host,
        "start_ms": candles[0].open_time_ms,
        "end_ms": candles[-1].open_time_ms,
        "n": len(candles),
        "cache_hit": False,
        "validation": report.to_dict(),
        "venue": "binance-spot",
        "note": "Public market data only. Incomplete final candle dropped. No rows were interpolated.",
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
    return candles, meta
