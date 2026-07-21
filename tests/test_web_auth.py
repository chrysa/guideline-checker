"""Unit tests for the multi-mode authentication module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx2 import ASGITransport, AsyncClient

from guideline_checker.web.auth import (
    AuthMode,
    _check_api_key,
    _check_local,
    _resolve_mode,
)

pytestmark = pytest.mark.anyio

# ── _resolve_mode ─────────────────────────────────────────────────────────────


def test_resolve_mode_disabled_via_auth_enabled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    assert _resolve_mode() == AuthMode.DISABLED


def test_resolve_mode_default_is_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    assert _resolve_mode() == AuthMode.API_KEY


def test_resolve_mode_explicit_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "local")
    assert _resolve_mode() == AuthMode.LOCAL


def test_resolve_mode_explicit_ldap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "ldap")
    assert _resolve_mode() == AuthMode.LDAP


def test_resolve_mode_explicit_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "oidc")
    assert _resolve_mode() == AuthMode.OIDC


def test_resolve_mode_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "bogus")
    with pytest.raises(ValueError, match="Unknown AUTH_MODE"):
        _resolve_mode()


# ── _check_api_key ────────────────────────────────────────────────────────────


def test_check_api_key_skips_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    # Should not raise
    _check_api_key(None)
    _check_api_key("anything")


def test_check_api_key_accepts_correct_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "secret-key")
    # Should not raise
    _check_api_key("secret-key")


def test_check_api_key_rejects_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("API_KEY", "correct-key")
    with pytest.raises(HTTPException) as exc_info:
        _check_api_key("wrong-key")
    assert exc_info.value.status_code == 403


def test_check_api_key_rejects_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("API_KEY", "correct-key")
    with pytest.raises(HTTPException) as exc_info:
        _check_api_key(None)
    assert exc_info.value.status_code == 403


# ── _check_local ──────────────────────────────────────────────────────────────


def test_check_local_skips_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_USERNAME", raising=False)
    monkeypatch.delenv("LOCAL_PASSWORD", raising=False)
    _check_local(None)


def test_check_local_accepts_correct_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.security import HTTPBasicCredentials

    monkeypatch.setenv("LOCAL_USERNAME", "admin")
    monkeypatch.setenv("LOCAL_PASSWORD", "s3cr3t")
    supplied = "s3cr3t"
    creds = HTTPBasicCredentials(username="admin", password=supplied)
    _check_local(creds)


def test_check_local_rejects_wrong_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials

    monkeypatch.setenv("LOCAL_USERNAME", "admin")
    monkeypatch.setenv("LOCAL_PASSWORD", "s3cr3t")
    supplied = "wrong"
    creds = HTTPBasicCredentials(username="admin", password=supplied)
    with pytest.raises(HTTPException) as exc_info:
        _check_local(creds)
    assert exc_info.value.status_code == 401


def test_check_local_rejects_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("LOCAL_USERNAME", "admin")
    monkeypatch.setenv("LOCAL_PASSWORD", "s3cr3t")
    with pytest.raises(HTTPException) as exc_info:
        _check_local(None)
    assert exc_info.value.status_code == 401


# ── require_auth via TestClient ───────────────────────────────────────────────


@pytest.fixture()
async def auth_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """TestClient with auth mocked at web app level."""

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("API_KEY", "test-key-123")

    from guideline_checker.web.app import _state, app

    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    with patch("guideline_checker.web.app._do_scan"):
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=transport, base_url="http://testserver") as c,
        ):
            yield c


async def test_api_results_requires_auth_key(auth_client: AsyncClient) -> None:
    """Without API key, /api/results should be forbidden."""
    response = await auth_client.get("/api/results")
    assert response.status_code == 403


async def test_api_results_accepts_valid_key(auth_client: AsyncClient) -> None:
    """With correct API key, /api/results should return 200."""
    response = await auth_client.get("/api/results", headers={"X-Api-Key": "test-key-123"})
    assert response.status_code == 200


async def test_api_scan_requires_auth_key(auth_client: AsyncClient) -> None:
    """Without API key, /api/scan should be forbidden."""
    with patch("guideline_checker.web.app._do_scan"):
        response = await auth_client.post("/api/scan")
    assert response.status_code == 403


async def test_api_constraints_requires_auth_key(auth_client: AsyncClient) -> None:
    """Without API key, /api/constraints should be forbidden."""
    response = await auth_client.get("/api/constraints")
    assert response.status_code == 403


async def test_health_no_auth_required(auth_client: AsyncClient) -> None:
    """/health must always be accessible without auth."""
    response = await auth_client.get("/health")
    assert response.status_code == 200


async def test_dashboard_no_auth_required(auth_client: AsyncClient) -> None:
    """Dashboard HTML must always be accessible without auth."""
    response = await auth_client.get("/")
    assert response.status_code == 200


async def test_dashboard_never_embeds_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the dashboard must NEVER embed the server-side API key.

    The dashboard is served on the public ``/`` route, so leaking the key into
    the HTML would hand it to every anonymous visitor and defeat ``api_key``
    authentication. The browser must instead obtain the key at runtime.
    """
    import guideline_checker.web.app as web_app

    canary = "super-secret-should-not-leak"
    monkeypatch.setenv("API_KEY", canary)

    web_app._state.results = []
    web_app._state.constraints = []
    web_app._state.timestamp = None
    web_app._state.running = False
    web_app._state.error = None

    transport = ASGITransport(app=web_app.app, raise_app_exceptions=True)
    with patch("guideline_checker.web.app._do_scan"):
        async with (
            web_app.app.router.lifespan_context(web_app.app),
            AsyncClient(transport=transport, base_url="http://testserver") as c,
        ):
            response = await c.get("/")

    assert response.status_code == 200
    assert canary not in response.text
    assert "__API_KEY__" not in response.text
    # The client must still know how to authenticate itself.
    assert "X-Api-Key" in response.text
    assert "sessionStorage" in response.text


async def test_disabled_mode_allows_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """With AUTH_ENABLED=false, all API endpoints should be accessible."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("API_KEY", raising=False)

    from guideline_checker.web.app import _state, app

    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    with patch("guideline_checker.web.app._do_scan"):
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=transport, base_url="http://testserver") as c,
        ):
            response = await c.get("/api/results")
            assert response.status_code == 200
            response = await c.get("/api/constraints")
            assert response.status_code == 200
