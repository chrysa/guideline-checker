"""Web dashboard for guideline-checker results."""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from guideline_checker.checker import RuleResult, run_checks

# ── Configuration ──────────────────────────────────────────────────────────────

_SCAN_ROOT: Path = Path(os.environ.get("SCAN_ROOT", "."))


# ── State ──────────────────────────────────────────────────────────────────────


@dataclass
class _ScanState:
    results: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str | None = None
    running: bool = False
    error: str | None = None


_state: _ScanState = _ScanState()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _serialize_results(results: list[RuleResult]) -> list[dict[str, Any]]:
    """Convert RuleResult list to JSON-serialisable dicts."""
    return [
        {
            "instruction": rr.instruction.path.name,
            "apply_to": rr.instruction.apply_to,
            "files_checked": rr.files_checked,
            "violations": [
                {
                    "file": str(v.file),
                    "line_number": v.line_number,
                    "line_content": v.line_content,
                    "rule": v.rule,
                    "severity": v.severity,
                }
                for v in rr.violations
            ],
        }
        for rr in results
    ]


def _do_scan() -> None:
    """Run a full compliance scan and update _state."""
    _state.running = True
    _state.error = None
    try:
        instructions_dir = _SCAN_ROOT / ".github" / "instructions"
        results = run_checks(_SCAN_ROOT, instructions_dir)
        _state.results = _serialize_results(results)
        _state.timestamp = datetime.now(UTC).isoformat()
    except Exception as exc:  # pragma: no cover
        _state.error = str(exc)
    finally:
        _state.running = False


# ── App lifecycle ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Run an initial scan in a background thread on startup."""
    thread = threading.Thread(target=_do_scan, daemon=True)
    thread.start()
    yield


# ── FastAPI app ────────────────────────────────────────────────────────────────

app: FastAPI = FastAPI(
    title="Guideline Checker",
    description="Dashboard for guideline compliance results",
    version="1.0.0",
    lifespan=_lifespan,
)

# ── Dashboard HTML ─────────────────────────────────────────────────────────────

_DASHBOARD_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guideline Checker Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .filter-btn { color: #6b7280; background: white; }
    .filter-btn.active { background: #1e40af; color: white; border-color: #1e40af; }
    .sev-error  { background: #fef2f2; border-color: #fca5a5; }
    .sev-warning{ background: #fff7ed; border-color: #fdba74; }
    .sev-info   { background: #eff6ff; border-color: #93c5fd; }
    .badge-error  { background: #fee2e2; color: #dc2626; }
    .badge-warning{ background: #ffedd5; color: #ea580c; }
    .badge-info   { background: #dbeafe; color: #2563eb; }
    .spinner { border: 3px solid #e5e7eb; border-top-color: #3b82f6;
               border-radius: 50%; width: 18px; height: 18px;
               animation: spin .8s linear infinite; display: inline-block; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body class="bg-gray-50 min-h-screen font-sans">

  <!-- Header -->
  <header class="bg-gray-900 text-white shadow-lg">
    <div class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-bold tracking-tight">&#x1F50D; Guideline Checker</h1>
        <p id="scan-time" class="text-gray-400 text-xs mt-0.5">&mdash;</p>
      </div>
      <button id="scan-btn"
        class="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2
               rounded text-sm font-medium transition-colors flex items-center gap-2"
        onclick="triggerScan()">
        Run Scan
      </button>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-6 py-6 space-y-5">

    <!-- Stats cards -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
      <div class="bg-white border border-gray-200 rounded-lg p-4 text-center shadow-sm">
        <div class="text-3xl font-bold text-red-500" id="count-error">&mdash;</div>
        <div class="text-xs text-gray-500 uppercase tracking-wide mt-1">Errors</div>
      </div>
      <div class="bg-white border border-gray-200 rounded-lg p-4 text-center shadow-sm">
        <div class="text-3xl font-bold text-orange-400" id="count-warning">&mdash;</div>
        <div class="text-xs text-gray-500 uppercase tracking-wide mt-1">Warnings</div>
      </div>
      <div class="bg-white border border-gray-200 rounded-lg p-4 text-center shadow-sm">
        <div class="text-3xl font-bold text-blue-500" id="count-info">&mdash;</div>
        <div class="text-xs text-gray-500 uppercase tracking-wide mt-1">Info</div>
      </div>
      <div class="bg-white border border-gray-200 rounded-lg p-4 text-center shadow-sm">
        <div class="text-3xl font-bold text-gray-700" id="count-files">&mdash;</div>
        <div class="text-xs text-gray-500 uppercase tracking-wide mt-1">Files Checked</div>
      </div>
    </div>

    <!-- Error banner -->
    <div id="error-banner"
      class="hidden bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded text-sm">
    </div>

    <!-- Loading -->
    <div id="loading" class="text-center py-10 text-gray-400 text-sm">
      <div class="spinner mx-auto mb-3"></div>
      Running initial scan&hellip;
    </div>

    <!-- Filters + Search -->
    <div id="controls" class="hidden flex flex-wrap gap-2 items-center">
      <div class="flex gap-1">
        <button class="filter-btn active px-3 py-1.5 rounded text-sm font-medium border"
          data-severity="all" onclick="setFilter('all')">All</button>
        <button class="filter-btn px-3 py-1.5 rounded text-sm font-medium border"
          data-severity="error" onclick="setFilter('error')">Errors</button>
        <button class="filter-btn px-3 py-1.5 rounded text-sm font-medium border"
          data-severity="warning" onclick="setFilter('warning')">Warnings</button>
        <button class="filter-btn px-3 py-1.5 rounded text-sm font-medium border"
          data-severity="info" onclick="setFilter('info')">Info</button>
      </div>
      <input id="search" type="text" placeholder="Search by file, rule&hellip;"
        class="ml-auto border border-gray-300 rounded px-3 py-1.5 text-sm
               focus:outline-none focus:ring-2 focus:ring-blue-300 w-56"
        oninput="renderViolations()">
    </div>

    <!-- Violations list -->
    <div id="violations-container" class="hidden space-y-3"></div>

    <!-- All clear -->
    <div id="all-clear" class="hidden text-center py-14">
      <div class="text-6xl mb-3">&#x2705;</div>
      <p class="text-green-600 font-semibold text-lg">All guidelines satisfied</p>
      <p class="text-gray-400 text-sm mt-1">No violations found in current filter.</p>
    </div>

  </main>

  <script>
    var _filter = 'all';
    var _data   = null;

    function esc(s) {
      return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function setFilter(sev) {
      _filter = sev;
      document.querySelectorAll('.filter-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.severity === sev);
      });
      renderViolations();
    }

    function flatViolations(results) {
      var all = [];
      (results || []).forEach(function(r) {
        (r.violations || []).forEach(function(v) {
          all.push(Object.assign({}, v, { instruction: r.instruction }));
        });
      });
      return all;
    }

    function renderViolations() {
      if (!_data) return;
      var search = (document.getElementById('search').value || '').toLowerCase();
      var all    = flatViolations(_data.results);
      var items  = all.filter(function(v) {
        var okSev    = _filter === 'all' || v.severity === _filter;
        var okSearch = !search
          || v.file.toLowerCase().indexOf(search) >= 0
          || v.rule.toLowerCase().indexOf(search) >= 0
          || (v.line_content || '').toLowerCase().indexOf(search) >= 0;
        return okSev && okSearch;
      });

      var container = document.getElementById('violations-container');
      var allClear  = document.getElementById('all-clear');

      if (items.length === 0) {
        container.classList.add('hidden');
        allClear.classList.remove('hidden');
        return;
      }

      allClear.classList.add('hidden');
      container.classList.remove('hidden');
      container.innerHTML = items.map(function(v) {
        return '<div class="bg-white border rounded-lg shadow-sm overflow-hidden sev-' + esc(v.severity) + '">'
          + '<div class="px-4 py-3 flex items-start gap-3">'
          + '<span class="badge-' + esc(v.severity)
          + ' px-2 py-0.5 rounded text-xs font-bold uppercase mt-0.5 flex-shrink-0">'
          + esc(v.severity) + '</span>'
          + '<div class="flex-1 min-w-0">'
          + '<div class="text-sm font-mono text-gray-800 truncate" title="' + esc(v.file) + '">'
          + esc(v.file) + ':' + esc(v.line_number) + '</div>'
          + '<div class="text-xs text-gray-500 mt-0.5 font-mono truncate">'
          + esc((v.line_content || '').trim()) + '</div>'
          + '<div class="mt-1.5 text-xs text-gray-600">'
          + '<span class="font-medium">Rule:</span> ' + esc(v.rule)
          + ' &nbsp;<span class="text-gray-400">&#8212; ' + esc(v.instruction) + '</span>'
          + '</div></div></div></div>';
      }).join('');
    }

    function updateStats(data) {
      var all    = flatViolations(data.results);
      var counts = { error: 0, warning: 0, info: 0 };
      all.forEach(function(v) { if (counts[v.severity] !== undefined) counts[v.severity]++; });
      var totalFiles = (data.results || []).reduce(function(s, r) { return s + (r.files_checked || 0); }, 0);

      document.getElementById('count-error').textContent   = counts.error;
      document.getElementById('count-warning').textContent = counts.warning;
      document.getElementById('count-info').textContent    = counts.info;
      document.getElementById('count-files').textContent   = totalFiles;

      var ts = data.timestamp ? new Date(data.timestamp).toLocaleString() : null;
      document.getElementById('scan-time').textContent = ts ? 'Last scan: ' + ts : '\u2014';
    }

    function render(data) {
      _data = data;

      var banner = document.getElementById('error-banner');
      if (data.error) {
        banner.textContent = 'Scan error: ' + data.error;
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
      }

      var loading = document.getElementById('loading');
      var controls = document.getElementById('controls');

      if (data.results !== null && !data.running) {
        loading.classList.add('hidden');
        controls.classList.remove('hidden');
        updateStats(data);
        renderViolations();
      }

      var btn = document.getElementById('scan-btn');
      btn.disabled = data.running;
      btn.innerHTML = data.running
        ? '<span class="spinner"></span> Scanning&hellip;'
        : 'Run Scan';
    }

    async function load() {
      try {
        var r = await fetch('/api/results');
        render(await r.json());
      } catch(_) {}
    }

    async function triggerScan() {
      document.getElementById('scan-btn').disabled = true;
      await fetch('/api/scan', { method: 'POST' });
      poll();
    }

    function poll() {
      setTimeout(async function() {
        var r    = await fetch('/api/results');
        var data = await r.json();
        render(data);
        if (data.running) poll();
      }, 1500);
    }

    load();
    setInterval(load, 60000);
  </script>
</body>
</html>"""


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Serve the interactive dashboard."""
    return _DASHBOARD_HTML


@app.post("/api/scan")
def trigger_scan(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Trigger a new compliance scan in the background."""
    if _state.running:
        return {"status": "already_running"}
    background_tasks.add_task(_do_scan)
    return {"status": "started"}


@app.get("/api/results")
def get_results() -> JSONResponse:
    """Return the latest scan results as JSON."""
    return JSONResponse(
        {
            "timestamp": _state.timestamp,
            "running": _state.running,
            "error": _state.error,
            "results": _state.results,
        }
    )
