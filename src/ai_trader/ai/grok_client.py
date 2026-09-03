"""Grok / xAI paper-analysis adapter.

Calls https://api.x.ai/v1/chat/completions with model grok-4.6 when
GROK_PAPER_ANALYSIS is explicitly enabled AND XAI_API_KEY is set.

Never talks to a broker. Never places orders. Never changes safety flags.
Missing key, timeout, or invalid JSON → HOLD.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ai_trader.ai.base import Analyst, ProposedDecision
from ai_trader.ai.payload import SYSTEM_PROMPT, build_grok_payload
from ai_trader.ai.validate import GROK_JSON_SCHEMA, parse_grok_response
from ai_trader.config import Settings
from ai_trader.types import Action, Decision, MarketAnalysis, MarketSnapshot, utc_now_iso

XAI_CHAT_PATH = "/chat/completions"
DEFAULT_MODEL = "grok-4.6"
DEFAULT_TIMEOUT = 8.0


class GrokAnalyst(Analyst):
    name = "grok"

    def __init__(
        self,
        settings: Settings,
        *,
        enable_paper: Optional[bool] = None,
        http_client: Any = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.settings = settings
        requested = settings.grok_paper_analysis if enable_paper is None else bool(enable_paper)
        # Env/flag enables PAPER ANALYSIS only. It cannot enable live trading.
        self.paper_requested = bool(requested)
        self.enabled = self.paper_requested
        self.timeout = float(
            timeout if timeout is not None else (settings.xai_timeout or DEFAULT_TIMEOUT)
        )
        self._http = http_client
        self.http_calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return self.settings.grok_configured()

    def _hold(
        self,
        snapshot: MarketSnapshot,
        analysis: Optional[MarketAnalysis],
        reason: str,
        *,
        raw: str = "",
        failure: str = "hold_fallback",
    ) -> ProposedDecision:
        symbol = analysis.symbol if analysis else (
            snapshot.bars[0].symbol if snapshot.bars else "SYSTEM"
        )
        ref = f"{symbol}:{analysis.as_of if analysis else snapshot.as_of}"
        decision = Decision(
            symbol=symbol,
            action=Action.HOLD,
            confidence=None,
            rationale=reason,
            model=self.settings.xai_model or DEFAULT_MODEL,
            raw_response=raw,
            market_snapshot_json=json.dumps({"failure": failure, "network": bool(self.http_calls)}),
            analysis_ref=ref,
            created_at=utc_now_iso(),
        )
        return ProposedDecision(
            decision=decision,
            context={
                "fixture": False,
                "network": bool(self.http_calls),
                "validated": False,
                "failure": failure,
                "analysis_ref": ref,
            },
        )

    def propose(
        self,
        snapshot: MarketSnapshot,
        analysis: Optional[MarketAnalysis] = None,
        *,
        account: Optional[dict[str, Any]] = None,
        positions: Optional[list] = None,
    ) -> ProposedDecision:
        if not self.paper_requested:
            return self._hold(
                snapshot,
                analysis,
                "Grok paper analysis is not enabled. Safe HOLD. Fixture remains the default.",
                failure="not_enabled",
            )
        if not self.is_configured():
            return self._hold(
                snapshot,
                analysis,
                "XAI_API_KEY is missing. Grok was not called. Safe HOLD.",
                failure="missing_api_key",
            )
        payload = build_grok_payload(analysis, account=account, positions=positions)
        body = {
            "model": self.settings.xai_model or DEFAULT_MODEL,
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "paper_decision",
                    "schema": GROK_JSON_SCHEMA,
                    "strict": True,
                },
            },
        }
        url = f"{self.settings.xai_base_url.rstrip('/')}{XAI_CHAT_PATH}"
        headers = {
            "Authorization": f"Bearer {self.settings.xai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            raw_text = self._post(url, headers, body)
        except Exception as exc:  # noqa: BLE001 — any transport failure is HOLD
            name = type(exc).__name__
            if "timeout" in name.lower() or "timeout" in str(exc).lower():
                return self._hold(
                    snapshot, analysis, "Grok request timed out. Converted to HOLD.", failure="timeout"
                )
            return self._hold(
                snapshot,
                analysis,
                f"Grok network error ({name}). Converted to HOLD.",
                failure="network_error",
            )
        parsed = parse_grok_response(raw_text)
        symbol = analysis.symbol if analysis else (
            snapshot.bars[0].symbol if snapshot.bars else "SYSTEM"
        )
        ref = f"{symbol}:{analysis.as_of if analysis else snapshot.as_of}"
        decision = Decision(
            symbol=symbol,
            action=parsed.action,
            confidence=parsed.confidence,
            rationale=parsed.reasoning,
            model=self.settings.xai_model or DEFAULT_MODEL,
            raw_response=parsed.raw,
            market_snapshot_json=json.dumps({"payload": payload, "validated": parsed.ok}),
            analysis_ref=ref,
            created_at=utc_now_iso(),
        )
        return ProposedDecision(
            decision=decision,
            context={
                "fixture": False,
                "network": True,
                "validated": parsed.ok,
                "failure": parsed.failure,
                "analysis_ref": ref,
                "model": decision.model,
            },
        )

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
        # Refuse any broker host. Check the token, never assign a live URL.
        if "alpaca" in url.lower():
            raise RuntimeError("Refusing to call a broker URL from GrokAnalyst.")
        if "tools" in body or "functions" in body:
            raise RuntimeError("GrokAnalyst must not send tools.")
        self.http_calls.append({"url": url, "model": body.get("model"), "has_tools": "tools" in body})
        client = self._http
        if client is None:
            import httpx

            with httpx.Client(timeout=self.timeout) as owned:
                response = owned.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        else:
            response = client.post(url, headers=headers, json=body, timeout=self.timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            data = response.json() if hasattr(response, "json") else response
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [part.get("text", "") for part in content if isinstance(part, dict)]
                    return "".join(parts)
            if "action" in data:
                return json.dumps(data)
        return json.dumps(data) if not isinstance(data, str) else data

    def health(self) -> dict:
        return {
            "name": self.name,
            "ready": bool(self.paper_requested and self.is_configured()),
            "configured": self.is_configured(),
            "enabled": self.enabled,
            "paper_requested": self.paper_requested,
            "model": self.settings.xai_model or DEFAULT_MODEL,
            "base_url": self.settings.xai_base_url,
            "network": bool(self.http_calls),
            "notes": (
                "Paper analysis only. POST /chat/completions model grok-4.6. "
                "Output is BUY/SELL/HOLD JSON. Never a broker call."
            ),
        }
