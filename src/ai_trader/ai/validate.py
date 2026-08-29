"""Strict validation of Grok paper-analysis responses.

Any failure becomes HOLD. Extra fields are rejected. Grok cannot carry
order, broker, sizing, or safety instructions.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ai_trader.types import Action

ALLOWED_KEYS = frozenset({"action", "confidence", "reasoning"})
ALLOWED_ACTIONS = frozenset({"BUY", "SELL", "HOLD"})
FORBIDDEN_HINTS = (
    "qty",
    "quantity",
    "order",
    "broker",
    "alpaca",
    "stop",
    "take_profit",
    "allow_orders",
    "kill_switch",
    "live_trading",
    "leverage",
    "notional",
    "tool",
    "function",
)

GROK_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "confidence", "reasoning"],
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
}


class GrokParseResult:
    def __init__(
        self,
        *,
        action: Action,
        confidence: Optional[float],
        reasoning: str,
        ok: bool,
        failure: Optional[str],
        raw: str,
    ) -> None:
        self.action = action
        self.confidence = confidence
        self.reasoning = reasoning
        self.ok = ok
        self.failure = failure
        self.raw = raw


def _hold(reason: str, raw: str = "") -> GrokParseResult:
    return GrokParseResult(
        action=Action.HOLD,
        confidence=None,
        reasoning=reason,
        ok=False,
        failure=reason,
        raw=raw,
    )


def extract_json_object(text: str) -> Optional[str]:
    if not text or not str(text).strip():
        return None
    cleaned = str(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    return cleaned[start : end + 1]


def parse_grok_response(raw: Any) -> GrokParseResult:
    if raw is None:
        return _hold("Grok response was empty.")
    if not isinstance(raw, str):
        raw = str(raw)
    blob = extract_json_object(raw)
    if not blob:
        return _hold("Grok response was not JSON.", raw)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return _hold("Grok JSON was malformed.", raw)
    if not isinstance(data, dict):
        return _hold("Grok JSON was not an object.", raw)
    keys = set(data.keys())
    if keys != ALLOWED_KEYS:
        return _hold("Grok JSON contained unsupported fields. Converted to HOLD.", raw)
    lowered = {str(k).lower() for k in keys}
    if any(hint in lowered for hint in FORBIDDEN_HINTS):
        return _hold("Grok JSON contained forbidden trading fields. Converted to HOLD.", raw)
    action_raw = data.get("action")
    if not isinstance(action_raw, str):
        return _hold("Grok action was invalid. Converted to HOLD.", raw)
    action = action_raw.strip().upper()
    if action not in ALLOWED_ACTIONS:
        return _hold("Grok action was invalid. Converted to HOLD.", raw)
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _hold("Grok confidence was invalid. Converted to HOLD.", raw)
    conf = float(confidence)
    if conf < 0.0 or conf > 1.0:
        return _hold("Grok confidence was out of range. Converted to HOLD.", raw)
    reasoning = data.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        return _hold("Grok reasoning was invalid. Converted to HOLD.", raw)
    if len(reasoning) > 2000:
        reasoning = reasoning[:2000]
    return GrokParseResult(
        action=Action(action),
        confidence=round(conf, 4),
        reasoning=reasoning.strip(),
        ok=True,
        failure=None,
        raw=raw,
    )
