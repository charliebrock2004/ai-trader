"""Read-only payload sent to Grok. No secrets, no order fields, no tools."""

from __future__ import annotations

from typing import Any, Optional

from ai_trader.types import MarketAnalysis


def build_grok_payload(
    analysis: Optional[MarketAnalysis],
    *,
    account: Optional[dict[str, Any]] = None,
    positions: Optional[list] = None,
) -> dict[str, Any]:
    analysis_dict = analysis.to_dict() if analysis else {}
    account = account or {}
    safe_account = {
        "currency": account.get("currency", "GBP"),
        "cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
        "account_equity": account.get("account_equity") or account.get("equity"),
        "invested_value": account.get("invested_value"),
        "unrealised_pnl": account.get("unrealised_pnl"),
        "realised_pnl": account.get("realised_pnl"),
        "halted": bool(account.get("halted")),
        "live": False,
    }
    safe_positions = []
    for pos in positions or account.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        safe_positions.append(
            {
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "quantity": pos.get("quantity"),
                "average_entry": pos.get("average_entry"),
                "current_price": pos.get("current_price"),
                "unrealised_pnl": pos.get("unrealised_pnl"),
            }
        )
    return {
        "mode": "paper_simulation",
        "live": False,
        "symbol": analysis_dict.get("symbol"),
        "current_price": analysis_dict.get("current_price"),
        "trend": analysis_dict.get("trend"),
        "sma": analysis_dict.get("sma"),
        "returns": analysis_dict.get("returns"),
        "volatility": analysis_dict.get("volatility"),
        "volume": analysis_dict.get("volume"),
        "analysis": {
            "timeframe": analysis_dict.get("timeframe"),
            "scenario": analysis_dict.get("scenario"),
            "bar_count": analysis_dict.get("bar_count"),
            "recent_high": analysis_dict.get("recent_high"),
            "recent_low": analysis_dict.get("recent_low"),
            "notes": analysis_dict.get("notes"),
        },
        "account": safe_account,
        "positions": safe_positions,
        "instructions": (
            "Return JSON only with keys action, confidence, reasoning. "
            "You do not place orders, size positions, or change safety settings."
        ),
    }


SYSTEM_PROMPT = (
    "You are Grok acting as a paper-trading market analyst for a simulated £100 account. "
    "You NEVER place orders, NEVER call tools, NEVER talk to a broker, and NEVER change "
    "risk limits, kill switch, or safety flags. "
    "Reply with JSON only: "
    '{"action":"BUY"|"SELL"|"HOLD","confidence":0-1,"reasoning":"..."}. '
    "confidence is a number between 0 and 1. If unsure, HOLD. "
    "Do not include quantity, price targets, stops, orders, or any other keys."
)
