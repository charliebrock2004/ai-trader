"""Grok / xAI paper-analysis adapter.

Calls https://api.x.ai/v1/chat/completions with grok-4.3 (cheapest current
general-purpose text model) when a key is present. The caller is expected to
have already passed the cheap deterministic filter and the daily budget.

Never talks to a broker. Never places orders. Never changes safety flags.
Missing key, timeout, invalid JSON, or a spent budget → HOLD.
A HOLD from this module is never a fabricated BUY/SELL.
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
DEFAULT_MODEL = "grok-4.3"
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
        budget: Any = None,
        on_usage: Any = None,
    ) -> None:
        self.settings = settings
        requested = settings.grok_paper_analysis if enable_paper is None else bool(enable_paper)
        # A deployed worker with a key should analyse paper candidates without
        # an extra flag. The flag still cannot enable live trading.
        if enable_paper is None and settings.grok_configured():
            requested = True
        # Env/flag enables PAPER ANALYSIS only. It cannot enable live trading.
        self.paper_requested = bool(requested)
        self.enabled = self.paper_requested
        self.timeout = float(
            timeout if timeout is not None else (settings.xai_timeout or DEFAULT_TIMEOUT)
        )
        self._http = http_client
        self.budget = budget
        self.on_usage = on_usage
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
        candidate: Optional[dict[str, Any]] = None,
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
        if self.budget is not None:
            ok, reason = self.budget.consume()
            if not ok:
                return self._hold(snapshot, analysis, reason, failure="budget")
        payload = build_grok_payload(
            analysis, account=account, positions=positions, candidate=candidate
        )
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
            raw_text, usage = self._post(url, headers, body)
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
        if usage and self.on_usage:
            self.on_usage(
                self.settings.xai_model or DEFAULT_MODEL,
                int(usage.get("prompt_tokens", 0) or 0),
                int(usage.get("completion_tokens", 0) or 0),
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

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
        usage = data.get("usage") if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    return content, usage
                if isinstance(content, list):
                    parts = [part.get("text", "") for part in content if isinstance(part, dict)]
                    return "".join(parts), usage
            if "action" in data:
                return json.dumps(data), usage
        text = json.dumps(data) if not isinstance(data, str) else data
        return text, usage

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
                "Paper analysis only. POST /chat/completions model grok-4.3. "
                "Output is BUY/SELL/HOLD JSON. Never a broker call. "
                "Grok is not consulted unless a cheap deterministic filter fires "
                "and the daily call budget still has room."
            ),
        }
