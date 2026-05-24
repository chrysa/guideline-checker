"""Tests for the web dashboard (FastAPI app)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from guideline_checker.web.app import _state, app


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient with the startup scan mocked out."""
    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    with patch("guideline_checker.web.app._do_scan"), TestClient(app) as c:
        yield c  # type: ignore[misc]


# ── /health ────────────────────────────────────────────────────────────────────


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── / (dashboard HTML) ─────────────────────────────────────────────────────────


def test_dashboard_returns_html(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_contains_expected_text(client: TestClient) -> None:
    response = client.get("/")
    assert "Guideline Checker" in response.text
    assert "api/scan" in response.text
    assert "api/results" in response.text


def test_dashboard_contains_constraints_tab(client: TestClient) -> None:
    response = client.get("/")
    assert "api/constraints" in response.text
    assert "tab-constraints" in response.text
    assert "switchTab" in response.text


# ── /api/results ───────────────────────────────────────────────────────────────


def test_api_results_empty_state(client: TestClient) -> None:
    response = client.get("/api/results")
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


def test_api_results_with_violations(client: TestClient) -> None:
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

    response = client.get("/api/results")
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


def test_api_results_shows_running_state(client: TestClient) -> None:
    _state.running = True
    response = client.get("/api/results")
    assert response.status_code == 200
    assert response.json()["running"] is True


def test_api_results_shows_error(client: TestClient) -> None:
    _state.error = "instructions directory not found"
    response = client.get("/api/results")
    assert response.status_code == 200
    assert response.json()["error"] == "instructions directory not found"


# ── /api/scan ──────────────────────────────────────────────────────────────────


def test_api_scan_starts_when_idle(client: TestClient) -> None:
    with patch("guideline_checker.web.app._do_scan"):
        response = client.post("/api/scan")
    assert response.status_code == 200
    assert response.json() == {"status": "started"}


def test_api_scan_rejects_when_already_running(client: TestClient) -> None:
    _state.running = True
    response = client.post("/api/scan")
    assert response.status_code == 200
    assert response.json() == {"status": "already_running"}


# ── _serialize_results ─────────────────────────────────────────────────────────


def test_serialize_results_empty() -> None:
    from guideline_checker.web.app import _serialize_results

    assert _serialize_results([]) == []


def test_serialize_results_maps_fields() -> None:
    from pathlib import Path

    from guideline_checker.checker import RuleResult, Violation
    from guideline_checker.web.app import _serialize_results

    instr = MagicMock()
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


def test_do_scan_happy_path() -> None:
    """_do_scan updates _state.results and clears running flag."""
    from guideline_checker.web.app import _do_scan

    _state.results = []
    _state.constraints = []
    _state.timestamp = None
    _state.running = False
    _state.error = None

    fake_rr = MagicMock()
    fake_rr.instruction.path.name = "test.instructions.md"
    fake_rr.instruction.apply_to = "**/*.py"
    fake_rr.files_checked = 2
    fake_rr.violations = []

    with (
        patch("guideline_checker.web.app.run_checks", return_value=[fake_rr]),
        patch("guideline_checker.web.app.load_all_sources", return_value=[]),
    ):
        _do_scan()

    assert _state.running is False
    assert _state.error is None
    assert _state.timestamp is not None
    assert len(_state.results) == 1
    assert _state.results[0]["instruction"] == "test.instructions.md"


def test_do_scan_sets_running_false_on_completion() -> None:
    """_do_scan always resets running to False even on success."""
    from guideline_checker.web.app import _do_scan

    _state.running = False

    with (
        patch("guideline_checker.web.app.run_checks", return_value=[]),
        patch("guideline_checker.web.app.load_all_sources", return_value=[]),
    ):
        _do_scan()

    assert _state.running is False


# ── /api/constraints ───────────────────────────────────────────────────────────


def test_api_constraints_empty_state(client: TestClient) -> None:
    response = client.get("/api/constraints")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert "total_rules" in data
    assert "total_sources" in data
    assert data["sources"] == []
    assert data["total_rules"] == 0
    assert data["total_sources"] == 0


def test_api_constraints_with_data(client: TestClient) -> None:
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
    response = client.get("/api/constraints")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sources"] == 1
    assert data["total_rules"] == 2
    assert data["sources"][0]["source_type"] == "claude"


def test_scan_state_has_constraints_field(client: TestClient) -> None:
    """_ScanState must have constraints initialised to []."""
    assert hasattr(_state, "constraints")
    assert isinstance(_state.constraints, list)
