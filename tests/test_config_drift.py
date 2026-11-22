"""Phase 0.5: one model default, and one switch for Grok review."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_trader.ai.grok_client import GrokAnalyst
from ai_trader.config import DEFAULT_XAI_MODEL, Settings
from ai_trader.runtime import get_runtime, reset_runtime

REPO = Path(__file__).resolve().parents[1]


def test_key_alone_does_not_enable_review(
    isolated_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    settings = Settings()
    assert settings.grok_configured() is True
    assert settings.grok_review_enabled() is False
    assert GrokAnalyst(settings).enabled is False


def test_switch_enables_review(isolated_env: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_PAPER_ANALYSIS", "true")
    settings = Settings()
    assert settings.grok_review_enabled() is True
    assert GrokAnalyst(settings).enabled is True


def test_key_alone_builds_no_analyst(
    isolated_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    from ai_trader.config import clear_settings_cache

    clear_settings_cache()
    reset_runtime()
    try:
        assert get_runtime().agent.analyst is None
    finally:
        reset_runtime()
        clear_settings_cache()


def test_model_default_is_declared_once() -> None:
    assert Settings.model_fields["xai_model"].default == DEFAULT_XAI_MODEL
    literal = re.compile(r"""["']grok-\d[\w.\-]*["']""")
    offenders = []
    for path in (REPO / "src" / "ai_trader").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel == "src/ai_trader/costs/ledger.py":  # price table, keyed by model
            continue
        hits = literal.findall(path.read_text(encoding="utf-8"))
        if rel == "src/ai_trader/config.py":
            hits = hits[1:]
        if hits:
            offenders.append((rel, hits))
    assert offenders == []


def test_env_example_and_render_use_the_default() -> None:
    for name in (".env.example", "render.yaml"):
        text = (REPO / name).read_text(encoding="utf-8")
        models = set(re.findall(r"grok-\d[\w.\-]*", text))
        assert models == {DEFAULT_XAI_MODEL}, name


def test_local_launcher_does_not_derive_the_switch_from_a_key() -> None:
    text = (REPO / "src/lib/paper-engine.server.ts").read_text(encoding="utf-8")
    body = text.split("function grokPaperFlag()", 1)[1].split("\n}", 1)[0]
    assert "XAI_API_KEY" not in body


def test_status_reports_key_without_review(
    isolated_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trader.config import clear_settings_cache
    from ai_trader.rpc import handle

    monkeypatch.setenv("XAI_API_KEY", "xai-test-key")
    clear_settings_cache()
    reset_runtime()
    try:
        usage = handle({"cmd": "status"})["grok_usage"]
        assert usage["connected"] is True
        assert usage["reviews_btc_candidates"] is False
    finally:
        reset_runtime()
        clear_settings_cache()
