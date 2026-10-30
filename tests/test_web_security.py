"""Security regression tests for the web dashboard (XSS hardening + headers)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient
from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    from guideline_checker.web.app import _state, app

    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    mocker.patch("guideline_checker.web.app._do_scan")
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://testserver") as c,
    ):
        yield c


async def test_dashboard_sets_security_headers(client: AsyncClient) -> None:
    response = await client.get("/")
    csp = response.headers.get("Content-Security-Policy", "")
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


async def test_api_responses_also_carry_security_headers(client: AsyncClient) -> None:
    response = await client.get("/api/results")
    assert "Content-Security-Policy" in response.headers


def test_client_side_escape_neutralises_attribute_breakout() -> None:
    """Regression: the dashboard's esc() must escape quotes, not only < > &.

    Without quote escaping, a scanned excerpt containing a double quote breaks
    out of a ``attr="${esc(x)}"`` context and injects markup — an XSS even
    though angle brackets are escaped.
    """
    import importlib.resources

    html = (importlib.resources.files("guideline_checker.web") / "static" / "index.html").read_text(encoding="utf-8")
    # The hardened escaper replaces both quote characters.
    assert '.replace(/"/g, "&quot;")' in html
    assert "replace(/'/g, \"&#39;\")" in html
