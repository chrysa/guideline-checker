"""Tests for the central aggregation server and the push CLI command."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx2 import ASGITransport, AsyncClient
from pytest_mock import MockerFixture

from guideline_checker.cli import _default_repo_name, _slug_repo, main
from guideline_checker.web.central import central_app

pytestmark = pytest.mark.anyio

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
async def client(store: Path) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=central_app, raise_app_exceptions=True)
    async with (
        central_app.router.lifespan_context(central_app),
        AsyncClient(transport=transport, base_url="http://testserver") as c,
    ):
        yield c


def _ingest_body(repo: str = "demo-repo", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"repo": repo, "summary": dict(_SUMMARY), "branch": "main", "commit": "abc123"}
    body.update(over)
    return body


# ── basic endpoints ─────────────────────────────────────────────────────────────


async def test_health(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_index_html(client: AsyncClient) -> None:
    res = await client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Central" in res.text


async def test_index_dashboard_consumes_history_endpoint(client: AsyncClient) -> None:
    # Drift guard: the dashboard must render the per-repo trend by fetching the
    # history endpoint — otherwise that endpoint is dead UI weight.
    html = (await client.get("/")).text
    assert "/history?limit=" in html
    assert "sparkline" in html
    assert "Error trend" in html


async def test_repos_empty_initially(client: AsyncClient) -> None:
    res = await client.get("/api/repos")
    assert res.status_code == 200
    assert res.json() == {"repos": [], "count": 0}


# ── ingest round-trip ───────────────────────────────────────────────────────────


async def test_ingest_then_list(client: AsyncClient, store: Path) -> None:
    res = await client.post("/api/ingest", json=_ingest_body())
    assert res.status_code == 200
    assert res.json() == {"status": "stored", "repo": "demo-repo"}
    assert (store / "demo-repo.json").is_file()

    listing = (await client.get("/api/repos")).json()
    assert listing["count"] == 1
    entry = listing["repos"][0]
    assert entry["repo"] == "demo-repo"
    assert entry["summary"]["errors"] == 2
    assert entry["received_at"]  # server-stamped


async def test_ingest_overwrites_latest(client: AsyncClient) -> None:
    await client.post("/api/ingest", json=_ingest_body(commit="old"))
    await client.post("/api/ingest", json=_ingest_body(commit="new", summary={**_SUMMARY, "errors": 0}))
    listing = (await client.get("/api/repos")).json()
    assert listing["count"] == 1
    assert listing["repos"][0]["commit"] == "new"
    assert listing["repos"][0]["summary"]["errors"] == 0


async def test_get_repo_returns_full_record(client: AsyncClient) -> None:
    await client.post("/api/ingest", json=_ingest_body(report={"summary": _SUMMARY, "rules": []}))
    res = await client.get("/api/repos/demo-repo")
    assert res.status_code == 200
    body = res.json()
    assert body["repo"] == "demo-repo"
    assert body["report"] == {"summary": _SUMMARY, "rules": []}


async def test_get_unknown_repo_404(client: AsyncClient) -> None:
    assert (await client.get("/api/repos/nope")).status_code == 404


@pytest.mark.parametrize("bad", ["../evil", "a/b", "with space", ""])
async def test_ingest_rejects_unsafe_repo_name(client: AsyncClient, bad: str) -> None:
    res = await client.post("/api/ingest", json=_ingest_body(repo=bad))
    assert res.status_code == 422


# ── history & trend ──────────────────────────────────────────────────────────────


async def test_history_accumulates_oldest_first(client: AsyncClient, store: Path) -> None:
    await client.post("/api/ingest", json=_ingest_body(commit="c1", summary={**_SUMMARY, "errors": 5}))
    await client.post("/api/ingest", json=_ingest_body(commit="c2", summary={**_SUMMARY, "errors": 3}))
    assert (store / "history" / "demo-repo.jsonl").is_file()

    body = (await client.get("/api/repos/demo-repo/history")).json()
    assert body["count"] == 2
    assert [e["commit"] for e in body["history"]] == ["c1", "c2"]  # oldest first
    assert [e["summary"]["errors"] for e in body["history"]] == [5, 3]
    # full report is not retained in history points
    assert "report" not in body["history"][0]


async def test_history_limit_keeps_most_recent(client: AsyncClient) -> None:
    for i in range(4):
        await client.post("/api/ingest", json=_ingest_body(commit=f"c{i}"))
    body = (await client.get("/api/repos/demo-repo/history", params={"limit": 2})).json()
    assert [e["commit"] for e in body["history"]] == ["c2", "c3"]


async def test_history_empty_for_unknown_repo(client: AsyncClient) -> None:
    body = (await client.get("/api/repos/never-seen/history")).json()
    assert body == {"repo": "never-seen", "history": [], "count": 0}


async def test_history_rejects_unsafe_repo_name(client: AsyncClient) -> None:
    assert (await client.get("/api/repos/..%2Fevil/history")).status_code == 404


# ── S2083 path-traversal regression (pythonsecurity:S2083) ─────────────────────


@pytest.mark.parametrize(
    "traversal",
    [
        "../secret",
        "../../etc/passwd",
        "valid/../escape",
        "a%2F..%2Fb",
    ],
)
async def test_get_repo_rejects_traversal_attempt(client: AsyncClient, traversal: str) -> None:
    """get_repo must return 404 (not serve arbitrary FS paths) for traversal-like names."""
    # URL-encoded slashes are decoded by the ASGI layer; the router hands the
    # decoded string to the endpoint, which must reject it before building the path.
    res = await client.get(f"/api/repos/{traversal}")
    assert res.status_code == 404


def test_safe_repo_path_raises_on_traversal(tmp_path: Path) -> None:
    """_safe_repo_path must raise ValueError when the candidate escapes the store dir."""
    from guideline_checker.web.central import _safe_repo_path

    with pytest.raises(ValueError, match="traversal"):
        _safe_repo_path(tmp_path, "../outside.json")


async def test_history_capped_at_limit(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("guideline_checker.web.central._HISTORY_LIMIT", 3)
    for i in range(5):
        await client.post("/api/ingest", json=_ingest_body(commit=f"c{i}"))
    body = (await client.get("/api/repos/demo-repo/history")).json()
    assert body["count"] == 3
    assert [e["commit"] for e in body["history"]] == ["c2", "c3", "c4"]


async def test_error_trend_in_listing(client: AsyncClient) -> None:
    # single snapshot → no trend yet
    await client.post("/api/ingest", json=_ingest_body(summary={**_SUMMARY, "errors": 4}))
    assert (await client.get("/api/repos")).json()["repos"][0]["error_trend"] is None
    # second snapshot with fewer errors → negative delta
    await client.post("/api/ingest", json=_ingest_body(summary={**_SUMMARY, "errors": 1}))
    assert (await client.get("/api/repos")).json()["repos"][0]["error_trend"] == -3


# ── auth ─────────────────────────────────────────────────────────────────────────


async def test_ingest_requires_key_when_configured(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    assert (await client.post("/api/ingest", json=_ingest_body())).status_code == 403
    ok = await client.post("/api/ingest", json=_ingest_body(), headers={"X-Api-Key": "s3cret"})
    assert ok.status_code == 200


# ── push helpers ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("chrysa/guideline-checker", "chrysa-guideline-checker"), ("a b!c", "a-b-c"), ("--x--", "x")],
)
def test_slug_repo(raw: str, expected: str) -> None:
    assert _slug_repo(raw) == expected


def test_default_repo_name_falls_back_to_cwd(mocker: MockerFixture) -> None:
    mocker.patch("guideline_checker.cli._git_output", return_value=None)
    assert _default_repo_name() == Path.cwd().name


# ── push command ─────────────────────────────────────────────────────────────────


def _write_report(path: Path) -> Path:
    path.write_text(json.dumps({"generated_at": "2026-06-10T00:00:00Z", "summary": _SUMMARY, "rules": []}))
    return path


def test_push_happy_path(tmp_path: Path, mocker: MockerFixture) -> None:
    report = _write_report(tmp_path / "guideline-report.json")
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int = 30) -> Any:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["key"] = request.headers.get("X-api-key")
        cm = mocker.MagicMock()
        cm.__enter__.return_value = mocker.MagicMock(status=200)
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
    mocker.patch("urllib.request.urlopen", side_effect=fake_urlopen)
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


# --- L1.5 configurable history limit ---


def test_resolve_history_limit_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var falls back to the 200-point default."""
    from guideline_checker.web import central as central_mod

    monkeypatch.delenv("CENTRAL_HISTORY_LIMIT", raising=False)
    assert central_mod._resolve_history_limit() == 200


def test_resolve_history_limit_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A positive env value overrides the default."""
    from guideline_checker.web import central as central_mod

    monkeypatch.setenv("CENTRAL_HISTORY_LIMIT", "5")
    assert central_mod._resolve_history_limit() == 5


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-3", ""])
def test_resolve_history_limit_invalid_falls_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """A non-positive or non-numeric env value falls back to the default."""
    from guideline_checker.web import central as central_mod

    monkeypatch.setenv("CENTRAL_HISTORY_LIMIT", bad)
    assert central_mod._resolve_history_limit() == 200


def test_append_history_trims_to_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_append_history keeps only the most recent _HISTORY_LIMIT points."""
    from guideline_checker.web import central as central_mod

    monkeypatch.setenv("CENTRAL_STORE", str(tmp_path))
    monkeypatch.setattr(central_mod, "_HISTORY_LIMIT", 3)
    for i in range(5):
        entry = central_mod.HistoryEntry(
            received_at="2026-01-01T00:00:00Z",
            summary=central_mod.ReportSummary(errors=i),
        )
        central_mod._append_history(entry, "demo")
    entries = central_mod._load_history("demo")
    assert [e.summary.errors for e in entries] == [2, 3, 4]
