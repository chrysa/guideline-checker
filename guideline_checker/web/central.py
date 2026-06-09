"""Central aggregation server for guideline-checker.

Each chrysa repo runs ``guideline-checker check --json`` in CI and pushes the
report here (``guideline-checker push`` or a plain HTTP POST). The server keeps
the latest snapshot per repo and presents a single multi-repo compliance view.

Storage is intentionally dependency-free: one JSON file per repo under
``CENTRAL_STORE`` (default ``./central-store``). Authentication reuses the same
env-driven contract as the single-repo dashboard (see
:mod:`guideline_checker.web.auth`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from guideline_checker.web.auth import require_auth

# ── Configuration ──────────────────────────────────────────────────────────────

# Repo identifiers are restricted to this charset so they map safely to a
# filename — no path separators, no traversal.
_REPO_PATTERN = r"^[A-Za-z0-9._-]+$"


def _store_dir() -> Path:
    """Return the (env-configurable) directory backing the report store."""
    return Path(os.environ.get("CENTRAL_STORE", "./central-store"))


# ── Models ─────────────────────────────────────────────────────────────────────


class ReportSummary(BaseModel):
    """Violation counts, mirroring the JSON reporter's ``summary`` block."""

    files_checked: int = 0
    total_violations: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0


class IngestPayload(BaseModel):
    """Body accepted by ``POST /api/ingest``."""

    repo: str = Field(min_length=1, max_length=200, pattern=_REPO_PATTERN)
    summary: ReportSummary
    commit: str | None = Field(default=None, max_length=200)
    branch: str | None = Field(default=None, max_length=200)
    generated_at: str | None = None
    # Optional full report (rules/violations) kept for drill-down.
    report: dict[str, Any] | None = None


class RepoRecord(BaseModel):
    """Stored snapshot for a single repo."""

    repo: str
    summary: ReportSummary
    commit: str | None = None
    branch: str | None = None
    generated_at: str | None = None
    received_at: str
    report: dict[str, Any] | None = None


# ── Store ──────────────────────────────────────────────────────────────────────


def _record_path(repo: str) -> Path:
    """Return the on-disk path for a repo's snapshot (repo is pre-validated)."""
    return _store_dir() / f"{repo}.json"


def _save_record(record: RepoRecord) -> None:
    store = _store_dir()
    store.mkdir(parents=True, exist_ok=True)
    _record_path(record.repo).write_text(record.model_dump_json(indent=2), encoding="utf-8")


def _load_record(repo: str) -> RepoRecord | None:
    path = _record_path(repo)
    if not path.is_file():
        return None
    try:
        return RepoRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _all_records() -> list[RepoRecord]:
    store = _store_dir()
    if not store.is_dir():
        return []
    records: list[RepoRecord] = []
    for path in sorted(store.glob("*.json")):
        try:
            records.append(RepoRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records


# ── App ────────────────────────────────────────────────────────────────────────

central_app: FastAPI = FastAPI(
    title="Guideline Checker — Central",
    description="Aggregated compliance view across all chrysa repos",
    version="1.0.0",
)


@central_app.get("/health", response_model=dict[str, str])
def health() -> dict[str, str]:
    return {"status": "ok"}


@central_app.post(
    "/api/ingest",
    response_model=dict[str, str],
    dependencies=[Depends(require_auth)],
)
def ingest(payload: IngestPayload) -> dict[str, str]:
    """Store (overwrite) the latest compliance snapshot for a repo."""
    record = RepoRecord(
        repo=payload.repo,
        summary=payload.summary,
        commit=payload.commit,
        branch=payload.branch,
        generated_at=payload.generated_at,
        received_at=datetime.now(UTC).isoformat(),
        report=payload.report,
    )
    _save_record(record)
    return {"status": "stored", "repo": record.repo}


@central_app.get("/api/repos", response_model=None, dependencies=[Depends(require_auth)])
def list_repos() -> JSONResponse:
    """Return the latest snapshot summary for every known repo."""
    repos = [
        {
            "repo": r.repo,
            "commit": r.commit,
            "branch": r.branch,
            "generated_at": r.generated_at,
            "received_at": r.received_at,
            "summary": r.summary.model_dump(),
        }
        for r in _all_records()
    ]
    return JSONResponse({"repos": repos, "count": len(repos)})


@central_app.get("/api/repos/{repo}", response_model=None, dependencies=[Depends(require_auth)])
def get_repo(repo: str) -> JSONResponse:
    """Return the full stored snapshot for one repo (404 if unknown)."""
    record = _load_record(repo)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No report for repo {repo!r}")
    return JSONResponse(record.model_dump())


@central_app.get("/", response_class=HTMLResponse, response_model=None)
def index() -> str:
    """Serve the aggregated multi-repo dashboard."""
    return _CENTRAL_HTML


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

_CENTRAL_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guideline Checker — Central</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 2rem;
           background: Canvas; color: CanvasText; }
    h1 { margin: 0 0 .25rem; font-size: 1.4rem; }
    p.sub { margin: 0 0 1.5rem; opacity: .7; }
    table { border-collapse: collapse; width: 100%; }
    th, td { text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #8884; }
    th { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .err { color: #d11; font-weight: 600; }
    .warn { color: #b80; }
    .ok { color: #2a2; }
    .muted { opacity: .6; }
    #msg { padding: 1rem; opacity: .7; }
  </style>
</head>
<body>
  <h1>Guideline Checker — Central</h1>
  <p class="sub">Aggregated compliance across all reporting repos.</p>
  <div id="msg">Loading&hellip;</div>
  <table id="tbl" hidden>
    <thead>
      <tr>
        <th>Repo</th><th>Branch</th><th class="num">Errors</th>
        <th class="num">Warnings</th><th class="num">Files</th><th>Last report</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <script>
    function getKey() {
      let k = sessionStorage.getItem('gc_api_key');
      if (k === null) {
        k = prompt('API key (blank if auth disabled):') || '';
        sessionStorage.setItem('gc_api_key', k);
      }
      return k;
    }
    function fmt(ts) { return ts ? new Date(ts).toLocaleString() : '—'; }
    async function load() {
      const msg = document.getElementById('msg');
      try {
        const res = await fetch('/api/repos', { headers: { 'X-Api-Key': getKey() } });
        if (res.status === 401 || res.status === 403) {
          sessionStorage.removeItem('gc_api_key');
          msg.textContent = 'Authentication failed — reload to re-enter the API key.';
          return;
        }
        const data = await res.json();
        const rows = document.getElementById('rows');
        rows.innerHTML = '';
        data.repos.sort((a, b) => (b.summary.errors - a.summary.errors) || a.repo.localeCompare(b.repo));
        for (const r of data.repos) {
          const s = r.summary;
          const cls = s.errors ? 'err' : (s.warnings ? 'warn' : 'ok');
          const tr = document.createElement('tr');
          tr.innerHTML =
            `<td>${r.repo}</td>` +
            `<td class="muted">${r.branch || '—'}</td>` +
            `<td class="num ${cls}">${s.errors}</td>` +
            `<td class="num">${s.warnings}</td>` +
            `<td class="num muted">${s.files_checked}</td>` +
            `<td class="muted">${fmt(r.received_at)}</td>`;
          rows.appendChild(tr);
        }
        msg.hidden = true;
        document.getElementById('tbl').hidden = false;
        if (!data.repos.length) { msg.hidden = false; msg.textContent = 'No reports yet.'; }
      } catch (e) {
        msg.textContent = 'Failed to load: ' + e;
      }
    }
    load();
  </script>
</body>
</html>"""
