from __future__ import annotations

from ai_trader.config import Settings


def test_public_view_hides_secrets(isolated_env: object, monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-should-not-leak")
    from ai_trader.config import clear_settings_cache

    clear_settings_cache()
    view = Settings().public_view()
    blob = str(view)
    assert "secret-should-not-leak" not in blob
    assert view["live_trading"] is False
    assert view["xai_configured"] is True
