"""Tests for the central aggregation server and the push CLI command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from guideline_checker.cli import _default_repo_name, _slug_repo, main
from guideline_checker.web.central import central_app

_SUMMARY: dict[str, int] = {
    "files_checked": 10,
    "total_violations": 3,
    "errors": 2,
    "warnings": 1,
    "info": 0,
}


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the central store at a temp dir and default to open auth."""
    target = tmp_path / "central-store"
    monkeypatch.setenv("CENTRAL_STORE", str(target))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("AUTH_MODE", "api_key")  # open while API_KEY is unset
    return target


@pytest.fixture()
def client(store: Path) -> TestClient:
    return TestClient(central_app)


def _ingest_body(repo: str = "demo-repo", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"repo": repo, "summary": dict(_SUMMARY), "branch": "main", "commit": "abc123"}
    body.update(over)
    return body


# ── basic endpoints ─────────────────────────────────────────────────────────────


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_index_html(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Central" in res.text


def test_repos_empty_initially(client: TestClient) -> None:
    res = client.get("/api/repos")
    assert res.status_code == 200
    assert res.json() == {"repos": [], "count": 0}


# ── ingest round-trip ───────────────────────────────────────────────────────────


def test_ingest_then_list(client: TestClient, store: Path) -> None:
    res = client.post("/api/ingest", json=_ingest_body())
    assert res.status_code == 200
    assert res.json() == {"status": "stored", "repo": "demo-repo"}
    assert (store / "demo-repo.json").is_file()

    listing = client.get("/api/repos").json()
    assert listing["count"] == 1
    entry = listing["repos"][0]
    assert entry["repo"] == "demo-repo"
    assert entry["summary"]["errors"] == 2
    assert entry["received_at"]  # server-stamped


def test_ingest_overwrites_latest(client: TestClient) -> None:
    client.post("/api/ingest", json=_ingest_body(commit="old"))
    client.post("/api/ingest", json=_ingest_body(commit="new", summary={**_SUMMARY, "errors": 0}))
    listing = client.get("/api/repos").json()
    assert listing["count"] == 1
    assert listing["repos"][0]["commit"] == "new"
    assert listing["repos"][0]["summary"]["errors"] == 0


def test_get_repo_returns_full_record(client: TestClient) -> None:
    client.post("/api/ingest", json=_ingest_body(report={"summary": _SUMMARY, "rules": []}))
    res = client.get("/api/repos/demo-repo")
    assert res.status_code == 200
    body = res.json()
    assert body["repo"] == "demo-repo"
    assert body["report"] == {"summary": _SUMMARY, "rules": []}


def test_get_unknown_repo_404(client: TestClient) -> None:
    assert client.get("/api/repos/nope").status_code == 404


@pytest.mark.parametrize("bad", ["../evil", "a/b", "with space", ""])
def test_ingest_rejects_unsafe_repo_name(client: TestClient, bad: str) -> None:
    res = client.post("/api/ingest", json=_ingest_body(repo=bad))
    assert res.status_code == 422


# ── auth ─────────────────────────────────────────────────────────────────────────


def test_ingest_requires_key_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.post("/api/ingest", json=_ingest_body()).status_code == 403
    ok = client.post("/api/ingest", json=_ingest_body(), headers={"X-Api-Key": "s3cret"})
    assert ok.status_code == 200


# ── push helpers ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("chrysa/guideline-checker", "chrysa-guideline-checker"), ("a b!c", "a-b-c"), ("--x--", "x")],
)
def test_slug_repo(raw: str, expected: str) -> None:
    assert _slug_repo(raw) == expected


def test_default_repo_name_falls_back_to_cwd() -> None:
    with patch("guideline_checker.cli._git_output", return_value=None):
        assert _default_repo_name() == Path.cwd().name


# ── push command ─────────────────────────────────────────────────────────────────


def _write_report(path: Path) -> Path:
    path.write_text(json.dumps({"generated_at": "2026-06-10T00:00:00Z", "summary": _SUMMARY, "rules": []}))
    return path


def test_push_happy_path(tmp_path: Path) -> None:
    report = _write_report(tmp_path / "guideline-report.json")
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int = 30) -> Any:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["key"] = request.headers.get("X-api-key")
        cm = MagicMock()
        cm.__enter__.return_value = MagicMock(status=200)
        return cm

    argv = [
        "push",
        "--server",
        "https://central.example.com/",
        "--report",
        str(report),
        "--repo",
        "my-repo",
        "--api-key",
        "k",
    ]
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        rc = main(argv)

    assert rc == 0
    assert captured["url"] == "https://central.example.com/api/ingest"
    assert captured["body"]["repo"] == "my-repo"
    assert captured["body"]["summary"]["errors"] == 2
    assert captured["key"] == "k"


def test_push_missing_report(tmp_path: Path) -> None:
    rc = main(["push", "--server", "https://x.example.com", "--report", str(tmp_path / "nope.json")])
    assert rc == 1


def test_push_rejects_non_http_server(tmp_path: Path) -> None:
    report = _write_report(tmp_path / "guideline-report.json")
    rc = main(["push", "--server", "file:///etc", "--report", str(report), "--repo", "r"])
    assert rc == 1


def test_push_report_without_summary(tmp_path: Path) -> None:
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"rules": []}))
    rc = main(["push", "--server", "https://x.example.com", "--report", str(bad), "--repo", "r"])
    assert rc == 1


# ── central launch command ───────────────────────────────────────────────────────


def test_central_launches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENTRAL_STORE", "placeholder")  # register for teardown restoration
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    store = tmp_path / "store"
    code = main(["central", "--store", str(store), "--port", "9091"])

    assert code == 0
    assert os.environ["CENTRAL_STORE"] == str(store.resolve())
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9091


def test_central_reload_uses_import_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENTRAL_STORE", "placeholder")
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app

    monkeypatch.setattr("uvicorn.run", fake_run)
    code = main(["central", "--store", str(tmp_path / "s"), "--reload"])

    assert code == 0
    assert captured["app"] == "guideline_checker.web.central:central_app"


def test_central_missing_uvicorn(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib

    real_import = importlib.import_module

    def raiser(name: str, *a: object, **k: object) -> object:
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", raiser)
    code = main(["central", "--store", "x"])
    assert code == 1
    assert "guideline-checker[web]" in capsys.readouterr().err
