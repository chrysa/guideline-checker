"""Tests for the viewer/workshop runtime mode gate (read-only isolation)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient
from pytest_mock import MockerFixture

from guideline_checker.web.mode import AppMode, require_workshop, resolve_mode

pytestmark = pytest.mark.anyio

# Every state-changing route, gated by require_workshop.
_MUTATION_ROUTES = (
    "/api/scan",
    "/api/scan-all",
    "/api/interpret",
    "/api/interpret/persist",
    "/api/propose",
    "/api/rules/detector",
    "/api/rules/resolve",
)


# ── resolve_mode ──────────────────────────────────────────────────────────────


def test_resolve_mode_default_is_workshop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GC_MODE", raising=False)
    assert resolve_mode() == AppMode.WORKSHOP


def test_resolve_mode_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GC_MODE", "viewer")
    assert resolve_mode() == AppMode.VIEWER


def test_resolve_mode_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GC_MODE", "bogus")
    with pytest.raises(ValueError, match="Unknown GC_MODE"):
        resolve_mode()


def test_require_workshop_blocks_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException, status

    monkeypatch.setenv("GC_MODE", "viewer")
    with pytest.raises(HTTPException) as exc_info:
        require_workshop()
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_require_workshop_allows_workshop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GC_MODE", "workshop")
    require_workshop()  # must not raise


# ── end-to-end via the app ────────────────────────────────────────────────────


@pytest.fixture()
async def viewer_client(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("GC_MODE", "viewer")

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


@pytest.mark.parametrize("route", _MUTATION_ROUTES)
async def test_viewer_mode_refuses_every_mutation(viewer_client: AsyncClient, route: str) -> None:
    from fastapi import status

    response = await viewer_client.post(route)
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_viewer_mode_still_allows_reads(viewer_client: AsyncClient) -> None:
    from fastapi import status

    for route in ("/api/results", "/api/constraints", "/health", "/"):
        response = await viewer_client.get(route)
        assert response.status_code == status.HTTP_200_OK
