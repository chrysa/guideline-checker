"""Central aggregation server for guideline-checker.

Each chrysa repo runs ``guideline-checker check --json`` in CI and pushes the
report here (``guideline-checker push`` or a plain HTTP POST). The server keeps
the latest snapshot per repo plus a bounded per-repo history, and presents a
single multi-repo compliance view with per-repo error-trend sparklines.

Storage is intentionally dependency-free: one JSON file per repo under
``CENTRAL_STORE`` (default ``./central-store``), plus an append-only
``history/<repo>.jsonl`` capped at ``_HISTORY_LIMIT`` points. Authentication
reuses the same env-driven contract as the single-repo dashboard (see
:mod:`guideline_checker.web.auth`).
"""

from __future__ import annotations

import os
import re
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


# Maximum history points kept per repo (oldest are dropped). Bounds file growth.
# Override with the ``CENTRAL_HISTORY_LIMIT`` env var; a non-positive or non-numeric
# value falls back to this default.
_DEFAULT_HISTORY_LIMIT = 200
_HISTORY_LIMIT_ENV = "CENTRAL_HISTORY_LIMIT"


def _resolve_history_limit() -> int:
    """Resolve the per-repo history cap from ``CENTRAL_HISTORY_LIMIT``, else the default."""
    raw = os.environ.get(_HISTORY_LIMIT_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return _DEFAULT_HISTORY_LIMIT
        if value > 0:
            return value
    return _DEFAULT_HISTORY_LIMIT


_HISTORY_LIMIT = _resolve_history_limit()


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


class HistoryEntry(BaseModel):
    """One point in a repo's compliance history (the full report is not kept)."""

    received_at: str
    commit: str | None = None
    branch: str | None = None
    summary: ReportSummary


# ── Store ──────────────────────────────────────────────────────────────────────


def _safe_repo_path(store: Path, filename: str) -> Path:
    """Resolve *filename* inside *store* and assert no traversal escapes the base.

    Raises ``ValueError`` when the resolved path would land outside *store*.
    This is a defence-in-depth measure: the primary guard is the Pydantic
    ``_REPO_PATTERN`` field validator, but we add an explicit containment check
    so Sonar's taint analysis (S2083) — which cannot reason about Pydantic
    schemas — is satisfied at the call site.
    """
    base = store.resolve()
    candidate = (store / filename).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"Path traversal detected for filename {filename!r}")
    return candidate


def _record_path(repo: str) -> Path:
    """Return the on-disk path for a repo's snapshot (repo is pre-validated)."""
    return _safe_repo_path(_store_dir(), f"{repo}.json")


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


def _history_path(repo: str) -> Path:
    """Return the append-only history log path for a repo (one JSON object per line)."""
    history_dir = _store_dir() / "history"
    return _safe_repo_path(history_dir, f"{repo}.jsonl")


def _append_history(entry: HistoryEntry, repo: str) -> None:
    """Append a point to the repo history, trimming to the last ``_HISTORY_LIMIT``."""
    path = _history_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    lines.append(entry.model_dump_json())
    path.write_text("\n".join(lines[-_HISTORY_LIMIT:]) + "\n", encoding="utf-8")


def _load_history(repo: str, limit: int | None = None) -> list[HistoryEntry]:
    """Return a repo's history oldest-first; ``limit`` keeps only the most recent points."""
    path = _history_path(repo)
    if not path.is_file():
        return []
    entries: list[HistoryEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(HistoryEntry.model_validate_json(line))
        except ValueError:
            continue
    return entries[-limit:] if limit is not None and limit > 0 else entries


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
    _append_history(
        HistoryEntry(
            received_at=record.received_at, commit=record.commit, branch=record.branch, summary=record.summary
        ),
        record.repo,
    )
    return {"status": "stored", "repo": record.repo}


def _error_trend(repo: str) -> int | None:
    """Return the error-count delta vs the previous snapshot (None if no prior point)."""
    history = _load_history(repo, limit=2)
    if len(history) < 2:
        return None
    return history[-1].summary.errors - history[-2].summary.errors


@central_app.get("/api/repos", response_model=None, dependencies=[Depends(require_auth)])
def list_repos() -> JSONResponse:
    """Return the latest snapshot summary for every known repo, with an error trend."""
    repos = [
        {
            "repo": r.repo,
            "commit": r.commit,
            "branch": r.branch,
            "generated_at": r.generated_at,
            "received_at": r.received_at,
            "summary": r.summary.model_dump(),
            "error_trend": _error_trend(r.repo),
        }
        for r in _all_records()
    ]
    return JSONResponse({"repos": repos, "count": len(repos)})


@central_app.get("/api/repos/{repo}/history", response_model=None, dependencies=[Depends(require_auth)])
def repo_history(repo: str, limit: int = 0) -> JSONResponse:
    """Return a repo's compliance history oldest-first (``limit`` keeps the most recent points)."""
    if not re.fullmatch(_REPO_PATTERN, repo):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown repo {repo!r}")
    entries = _load_history(repo, limit=limit or None)
    return JSONResponse({"repo": repo, "history": [e.model_dump() for e in entries], "count": len(entries)})


@central_app.get("/api/repos/{repo}", response_model=None, dependencies=[Depends(require_auth)])
def get_repo(repo: str) -> JSONResponse:
    """Return the full stored snapshot for one repo (404 if unknown)."""
    if not re.fullmatch(_REPO_PATTERN, repo):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown repo {repo!r}")
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
    .spark { vertical-align: middle; }
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
        <th class="num">Warnings</th><th class="num">Files</th><th>Error trend</th><th>Last report</th>
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
    function trend(t) {
      if (t === null || t === undefined) return '';
      if (t > 0) return ` <span class="err" title="+${t} vs previous">▲</span>`;
      if (t < 0) return ` <span class="ok" title="${t} vs previous">▼</span>`;
      return ' <span class="muted" title="no change">→</span>';
    }
    function sparkline(points) {
      // Inline SVG polyline of the error count over the repo's report history.
      if (points.length < 2) return '<span class="muted" title="not enough history">—</span>';
      const w = 84, h = 18, pad = 2;
      const max = Math.max(...points), min = Math.min(...points);
      const span = (max - min) || 1;
      const stepX = (w - pad * 2) / (points.length - 1);
      const coords = points.map((v, i) => {
        const x = pad + i * stepX;
        const y = h - pad - ((v - min) / span) * (h - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      const cls = points[points.length - 1] ? 'err' : 'ok';
      const label = `Error history (${points.length} reports): ${points[0]} → ${points[points.length - 1]}`;
      return `<svg class="spark ${cls}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" ` +
             `role="img" aria-label="${label}"><title>${label}</title>` +
             `<polyline fill="none" stroke="currentColor" stroke-width="1.5" ` +
             `stroke-linejoin="round" stroke-linecap="round" points="${coords}"/></svg>`;
    }
    async function loadSparklines(cells) {
      // Lazily fill each repo's trend cell from /api/repos/{repo}/history.
      await Promise.all(cells.map(async ({ repo, cell }) => {
        try {
          const res = await fetch(`/api/repos/${encodeURIComponent(repo)}/history?limit=20`,
                                  { headers: { 'X-Api-Key': getKey() } });
          if (!res.ok) { cell.textContent = '—'; return; }
          const data = await res.json();
          cell.classList.remove('muted');
          cell.innerHTML = sparkline(data.history.map(e => e.summary.errors));
        } catch (e) {
          cell.textContent = '—';
        }
      }));
    }
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
        const sparkCells = [];
        for (const r of data.repos) {
          const s = r.summary;
          const cls = s.errors ? 'err' : (s.warnings ? 'warn' : 'ok');
          const tr = document.createElement('tr');
          tr.innerHTML =
            `<td>${r.repo}</td>` +
            `<td class="muted">${r.branch || '—'}</td>` +
            `<td class="num ${cls}">${s.errors}${trend(r.error_trend)}</td>` +
            `<td class="num">${s.warnings}</td>` +
            `<td class="num muted">${s.files_checked}</td>`;
          const sparkTd = document.createElement('td');
          sparkTd.className = 'muted';
          sparkTd.textContent = '…';
          tr.appendChild(sparkTd);
          const lastTd = document.createElement('td');
          lastTd.className = 'muted';
          lastTd.textContent = fmt(r.received_at);
          tr.appendChild(lastTd);
          rows.appendChild(tr);
          sparkCells.push({ repo: r.repo, cell: sparkTd });
        }
        msg.hidden = true;
        document.getElementById('tbl').hidden = false;
        if (!data.repos.length) { msg.hidden = false; msg.textContent = 'No reports yet.'; }
        loadSparklines(sparkCells);
      } catch (e) {
        msg.textContent = 'Failed to load: ' + e;
      }
    }
    load();
  </script>
</body>
</html>"""
