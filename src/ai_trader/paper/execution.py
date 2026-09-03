"""Deterministic simulated fills.

Assumptions (paper only):

* Spread: SPREAD_BPS (default 5) of price. Buyer pays half, seller receives half.
* Slippage: SLIPPAGE_BPS (default 5) of price, always adverse.
* Signal is evaluated on bar i CLOSE using candles[0..i] only (no look-ahead).
* Entry fills at bar i+1 OPEN ± spread/slippage. If there is no next bar, the
  order stays PENDING — fills are not guaranteed.
* Stop-loss and take-profit use subsequent bars' high/low after the fill.
* **Stops are NOT assumed to fill at the stop price.** A long stop fills at
  ``min(bar_open, stop_price)`` before costs, so a bar that gaps through the
  stop realises the gap. Take-profits get the opposite treatment: they fill at
  the target, never better, so a favourable gap is not credited.
* Ambiguous candle (range crosses both stop and target): STOP is assumed first.
  Favourable outcomes are never assumed.
* Long-only. No leverage. No shorts. No commissions (spread is the cost).
* No broker, no network, no live prices.

Prices here are in the instrument's quote currency. Conversion to the account's
base currency happens in the ledger, never here.
"""

from __future__ import annotations

from typing import Optional

from ai_trader.types import Candle

SPREAD_BPS = 5.0
SLIPPAGE_BPS = 5.0
BPS = 0.0001

ASSUMPTIONS = __doc__


def _bps(price: float, bps: float) -> float:
    return price * bps * BPS


def buy_fill_price(raw: float, *, spread_bps: float = SPREAD_BPS, slip_bps: float = SLIPPAGE_BPS) -> float:
    return round(raw + _bps(raw, spread_bps) / 2.0 + _bps(raw, slip_bps), 8)


def sell_fill_price(raw: float, *, spread_bps: float = SPREAD_BPS, slip_bps: float = SLIPPAGE_BPS) -> float:
    return round(raw - _bps(raw, spread_bps) / 2.0 - _bps(raw, slip_bps), 8)


def stop_exit_price(
    *,
    stop_loss: float,
    candle: Candle,
    spread_bps: float = SPREAD_BPS,
    slip_bps: float = SLIPPAGE_BPS,
) -> float:
    """Realistic long-stop exit for a bar that triggered the stop.

    If the bar opened below the stop the position could not have exited at the
    stop — the market was already through it. Fill at the open in that case,
    then apply the usual adverse spread and slippage. Never fills better than
    the stop.
    """
    reference = min(float(candle.open), float(stop_loss))
    return sell_fill_price(reference, spread_bps=spread_bps, slip_bps=slip_bps)


def target_exit_price(
    *,
    take_profit: float,
    candle: Candle,
    spread_bps: float = SPREAD_BPS,
    slip_bps: float = SLIPPAGE_BPS,
) -> float:
    """Long take-profit exit. Fills at the target, never at a favourable gap."""
    reference = float(take_profit)
    return sell_fill_price(reference, spread_bps=spread_bps, slip_bps=slip_bps)


def resolve_intrabar(
    *,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    candle: Candle,
) -> Optional[str]:
    """Return 'stop', 'target', or None. Conservative if both would hit."""
    hit_stop = stop_loss is not None and candle.low <= stop_loss
    hit_target = take_profit is not None and candle.high >= take_profit
    if hit_stop and hit_target:
        return "stop"
    if hit_stop:
        return "stop"
    if hit_target:
        return "target"
    return None
