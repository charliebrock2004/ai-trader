"""Technical analysis over a validated candle series.

Read-only. Never places an order. Never talks to a broker. Not a trade signal.
"""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.analysis.base import MarketAnalyst
from ai_trader.analysis.indicators import (
    RANGE_WINDOW,
    RETURN_LOOKBACKS,
    SMA_WINDOWS,
    abs_change,
    classify_trend,
    mean,
    pct_change,
    price_vs_sma,
    rolling_volatility,
    sma,
    sma_slope,
)
from ai_trader.types import (
    AnalysisBundle,
    CandleSeries,
    MarketAnalysis,
    MarketSnapshot,
    utc_now_iso,
)


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def analyse_series(series: CandleSeries) -> MarketAnalysis:
    candles = series.candles
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    last = series.last()
    price = last.close if last else None

    sma_values = {window: sma(closes, window) for window in SMA_WINDOWS}
    slopes = {window: sma_slope(closes, window) for window in (5, 10, 20)}
    vs_sma = {window: price_vs_sma(price, sma_values[window]) for window in SMA_WINDOWS}

    lookbacks = {str(period): _round(pct_change(closes, period)) for period in RETURN_LOOKBACKS}

    last_range = (last.high - last.low) if last else None
    last_range_pct = None
    if last and last.close:
        last_range_pct = last_range / last.close if last_range is not None else None

    ranges = [c.high - c.low for c in candles[-RANGE_WINDOW:]]
    range_pcts = [
        (c.high - c.low) / c.close for c in candles[-RANGE_WINDOW:] if c.close
    ]

    last_volume = last.volume if last else None
    avg_volume = mean(volumes[-RANGE_WINDOW:]) if volumes else None
    vs_average = None
    if last_volume is not None and avg_volume:
        vs_average = last_volume / avg_volume

    anchor = sma_values[20] or sma_values[10] or sma_values[5]
    slope = slopes[20] if sma_values[20] is not None else (
        slopes[10] if sma_values[10] is not None else slopes[5]
    )
    trend = classify_trend(price, anchor, slope)

    notes = (
        "Read-only technical summary. Not a trade signal. "
        "No order is implied."
    )
    if len(candles) < 50:
        notes += " SMA 50 omitted — not enough bars."

    return MarketAnalysis(
        symbol=series.symbol,
        timeframe=series.timeframe,
        scenario=series.scenario,
        as_of=last.timestamp if last else utc_now_iso(),
        bar_count=len(candles),
        current_price=_round(price, 4),
        recent_high=_round(max(highs), 4) if highs else None,
        recent_low=_round(min(lows), 4) if lows else None,
        trend=trend,
        last_abs=_round(abs_change(closes, 1), 4),
        last_pct=_round(pct_change(closes, 1)),
        lookbacks=lookbacks,
        sma_5=_round(sma_values[5], 4),
        sma_10=_round(sma_values[10], 4),
        sma_20=_round(sma_values[20], 4),
        sma_50=_round(sma_values[50], 4),
        slope_5=_round(slopes[5]),
        slope_10=_round(slopes[10]),
        slope_20=_round(slopes[20]),
        price_vs_sma_5=_round(vs_sma[5]),
        price_vs_sma_10=_round(vs_sma[10]),
        price_vs_sma_20=_round(vs_sma[20]),
        price_vs_sma_50=_round(vs_sma[50]),
        last_range=_round(last_range, 4),
        last_range_pct=_round(last_range_pct),
        average_range=_round(mean(ranges), 4),
        average_range_pct=_round(mean(range_pcts)),
        rolling_volatility=_round(rolling_volatility(closes)),
        last_volume=_round(last_volume, 2),
        average_volume=_round(avg_volume, 2),
        volume_vs_average=_round(vs_average),
        notes=notes,
        source="technical",
    )


class TechnicalAnalyst(MarketAnalyst):
    name = "technical"

    def analyse(self, snapshot: MarketSnapshot) -> AnalysisBundle:
        analyses = tuple(analyse_series(series) for series in snapshot.series)
        return AnalysisBundle(as_of=snapshot.as_of, analyses=analyses)

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ready": True,
            "notes": "Read-only SMAs, returns, trend, and volume. Not a trade signal.",
        }
