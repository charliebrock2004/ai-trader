"""Security (18 Nov 2026): no dashboard page may load the grok.com app-builder
"extensions.js" script. It ran with full access to every page it was on."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "ai_trader" / "dashboard" / "templates"


def test_no_template_references_the_grok_script() -> None:
    files = sorted(TEMPLATES.glob("*.html"))
    assert files, "dashboard templates exist"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "grok-app-builder" not in text, path.name
        assert "grok.com" not in text, path.name


def test_rendered_dashboard_pages_load_no_grok_script(client: TestClient) -> None:
    for route in ("/", "/paper", "/performance", "/system"):
        page = client.get(route)
        assert page.status_code == 200, route
        assert "grok-app-builder/extensions.js" not in page.text, route
