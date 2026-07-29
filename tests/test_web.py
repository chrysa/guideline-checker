"""Tests for the web dashboard (FastAPI app)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient
from pytest_mock import MockerFixture

from guideline_checker.web.app import _state, app

pytestmark = pytest.mark.anyio


@pytest.fixture()
async def client(mocker: MockerFixture) -> AsyncIterator[AsyncClient]:
    """Return a TestClient with the startup scan mocked out."""
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


# ── /health ────────────────────────────────────────────────────────────────────


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── / (dashboard HTML) ─────────────────────────────────────────────────────────


async def test_dashboard_returns_html(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_dashboard_contains_expected_text(client: AsyncClient) -> None:
    response = await client.get("/")
    assert "guideline-checker" in response.text
    assert "api/scan" in response.text
    assert "api/rules-health" in response.text


async def test_dashboard_exposes_the_workshop_surface(client: AsyncClient) -> None:
    response = await client.get("/")
    assert "api/propose" in response.text
    assert "Propose" in response.text
    assert "workshop" in response.text.lower()


# ── /api/results ───────────────────────────────────────────────────────────────


async def test_api_results_empty_state(client: AsyncClient) -> None:
    response = await client.get("/api/results")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "running" in data
    assert "timestamp" in data
    assert "error" in data
    assert data["results"] == []
    assert data["running"] is False
    assert data["timestamp"] is None
    assert data["error"] is None


async def test_api_results_with_violations(client: AsyncClient) -> None:
    _state.results = [
        {
            "instruction": "python.instructions.md",
            "apply_to": "**/*.py",
            "files_checked": 3,
            "violations": [
                {
                    "file": "src/main.py",
                    "line_number": 42,
                    "line_content": "import pdb",
                    "rule": "No debug imports",
                    "severity": "error",
                }
            ],
        }
    ]
    _state.timestamp = "2024-06-01T10:00:00+00:00"

    response = await client.get("/api/results")
    assert response.status_code == 200
    data = response.json()
    assert data["timestamp"] == "2024-06-01T10:00:00+00:00"
    assert len(data["results"]) == 1
    result = data["results"][0]
    assert result["instruction"] == "python.instructions.md"
    assert result["files_checked"] == 3
    assert len(result["violations"]) == 1
    v = result["violations"][0]
    assert v["file"] == "src/main.py"
    assert v["line_number"] == 42
    assert v["severity"] == "error"
    assert v["rule"] == "No debug imports"


async def test_api_results_shows_running_state(client: AsyncClient) -> None:
    _state.running = True
    response = await client.get("/api/results")
    assert response.status_code == 200
    assert response.json()["running"] is True


async def test_api_results_shows_error(client: AsyncClient) -> None:
    _state.error = "instructions directory not found"
    response = await client.get("/api/results")
    assert response.status_code == 200
    assert response.json()["error"] == "instructions directory not found"


# ── /api/scan ──────────────────────────────────────────────────────────────────


async def test_api_scan_starts_when_idle(client: AsyncClient, mocker: MockerFixture) -> None:
    mocker.patch("guideline_checker.web.app._do_scan")
    response = await client.post("/api/scan")
    assert response.status_code == 200
    assert response.json() == {"status": "started"}


async def test_api_scan_rejects_when_already_running(client: AsyncClient) -> None:
    _state.running = True
    response = await client.post("/api/scan")
    assert response.status_code == 200
    assert response.json() == {"status": "already_running"}


# ── /api/projects + project switch ──────────────────────────────────────────────


async def test_api_projects_lists_workspace(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from guideline_checker.workspace import Project

    monkeypatch.setattr(
        "guideline_checker.web.app.discover_projects",
        lambda _ws: [Project("alpha", "/w/alpha"), Project("beta", "/w/beta")],
    )
    response = await client.get("/api/projects")
    assert response.status_code == 200
    assert [p["name"] for p in response.json()["projects"]] == ["alpha", "beta"]


async def test_api_scan_switches_to_a_known_project(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from guideline_checker.workspace import Project

    monkeypatch.setattr("guideline_checker.web.app.discover_projects", lambda _ws: [Project("alpha", "/w/alpha")])
    _state.active_project = None
    mocker.patch("guideline_checker.web.app._do_scan")
    response = await client.post("/api/scan", json={"project": "alpha"})
    assert response.status_code == 200
    assert _state.active_project == "/w/alpha"


async def test_api_scan_rejects_an_unknown_project(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("guideline_checker.web.app.discover_projects", lambda _ws: [])
    response = await client.post("/api/scan", json={"project": "nope"})
    assert response.status_code == 404


# ── _serialize_results ─────────────────────────────────────────────────────────


def test_serialize_results_empty() -> None:
    from guideline_checker.web.app import _serialize_results

    assert _serialize_results([]) == []


def test_serialize_results_maps_fields(mocker: MockerFixture) -> None:
    from pathlib import Path

    from guideline_checker.checker import RuleResult, Violation
    from guideline_checker.web.app import _serialize_results

    instr = mocker.MagicMock()
    instr.path = Path("python.instructions.md")
    instr.apply_to = "**/*.py"

    violation = Violation(
        file=Path("src/foo.py"),
        line_number=7,
        line_content="print('debug')",
        rule="No print statements",
        severity="warning",
    )
    rr = RuleResult(instruction=instr, violations=[violation], files_checked=5)

    result = _serialize_results([rr])
    assert len(result) == 1
    assert result[0]["instruction"] == "python.instructions.md"
    assert result[0]["apply_to"] == "**/*.py"
    assert result[0]["files_checked"] == 5
    v = result[0]["violations"][0]
    assert v["file"] == "src/foo.py"
    assert v["line_number"] == 7
    assert v["line_content"] == "print('debug')"
    assert v["rule"] == "No print statements"
    assert v["severity"] == "warning"


# ── _do_scan ───────────────────────────────────────────────────────────────────


def test_do_scan_happy_path(mocker: MockerFixture) -> None:
    """_do_scan updates _state.results and clears running flag."""
    from guideline_checker.web.app import _do_scan

    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    fake_rr = mocker.MagicMock()
    fake_rr.instruction.path.name = "test.instructions.md"
    fake_rr.instruction.apply_to = "**/*.py"
    fake_rr.files_checked = 2
    fake_rr.violations = []

    mocker.patch("guideline_checker.web.app.run_checks", return_value=[fake_rr])
    mocker.patch("guideline_checker.web.app.load_all_sources", return_value=[])

    _do_scan()

    assert _state.running is False
    assert _state.error is None
    assert _state.timestamp is not None
    assert len(_state.results) == 1
    assert _state.results[0]["instruction"] == "test.instructions.md"


def test_do_scan_sets_running_false_on_completion(mocker: MockerFixture) -> None:
    """_do_scan always resets running to False even on success."""
    from guideline_checker.web.app import _do_scan

    _state.running = False

    mocker.patch("guideline_checker.web.app.run_checks", return_value=[])
    mocker.patch("guideline_checker.web.app.load_all_sources", return_value=[])

    _do_scan()

    assert _state.running is False


# ── /api/constraints ───────────────────────────────────────────────────────────


async def test_api_constraints_empty_state(client: AsyncClient) -> None:
    response = await client.get("/api/constraints")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert "total_rules" in data
    assert "total_sources" in data
    assert data["sources"] == []
    assert data["total_rules"] == 0
    assert data["total_sources"] == 0


async def test_api_constraints_with_data(client: AsyncClient) -> None:
    _state.constraints = [
        {
            "name": "CLAUDE.md",
            "path": "/project/CLAUDE.md",
            "source_type": "claude",
            "description": "Claude — CLAUDE.md",
            "apply_to": "**/*",
            "rule_count": 2,
            "rules": ["Never hardcode secrets", "Always use type annotations"],
        }
    ]
    response = await client.get("/api/constraints")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sources"] == 1
    assert data["total_rules"] == 2
    assert data["sources"][0]["source_type"] == "claude"


async def test_scan_state_has_constraints_field(client: AsyncClient) -> None:
    """_ScanState must have constraints initialised to []."""
    assert hasattr(_state, "constraints")
    assert isinstance(_state.constraints, list)
