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

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from guideline_checker.checker import RuleResult, run_checks
from guideline_checker.loader import InstructionFile, RuleDetector, load_all_sources
from guideline_checker.persist import apply_detector
from guideline_checker.proposer import HeuristicProposer, OllamaProposer, Proposal
from guideline_checker.rule_health import RuleHealth, compute_rule_health, summarize
from guideline_checker.sandbox import replay
from guideline_checker.web.auth import require_auth

# ── Configuration ──────────────────────────────────────────────────────────────

_SCAN_ROOT: Path = Path(os.environ.get("SCAN_ROOT", "."))
# NOTE: the server-side API key is deliberately NOT read here. The dashboard
# HTML is served on the public ``/`` route, so embedding the key would leak it
# to every anonymous visitor and defeat ``api_key`` authentication entirely.
# Instead the browser asks the user for the key at runtime (see the dashboard
# JS), keeps it in sessionStorage, and sends it via the ``X-Api-Key`` header.
# Authentication logic itself lives in guideline_checker.web.auth.


# ── State ──────────────────────────────────────────────────────────────────────


@dataclass
class _ScanState:
    results: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    health: list[dict[str, Any]] = field(default_factory=list)
    health_summary: dict[str, int] = field(default_factory=dict)
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


def _serialize_health(health: list[RuleHealth]) -> list[dict[str, Any]]:
    """Convert RuleHealth list to JSON-serialisable dicts, worst state first."""
    order = {"dead": 0, "suspect": 1, "armed": 2, "proven": 3, "advisory": 4}
    ranked = sorted(health, key=lambda h: (order[h.state.value], h.instruction, h.rule))
    return [
        {
            "rule": h.rule,
            "instruction": h.instruction,
            "state": h.state.value,
            "has_declarative_detector": h.has_declarative_detector,
            "has_phrase_detection": h.has_phrase_detection,
            "fire_count": h.fire_count,
            "reason": h.reason,
        }
        for h in ranked
    ]


def _serialize_constraints(sources: list[InstructionFile]) -> list[dict[str, Any]]:
    """Convert InstructionFile list to JSON-serialisable constraint dicts."""
    return [
        {
            "name": src.path.name,
            "path": str(src.path),
            "source_type": src.source_type.value,
            "description": src.description,
            "apply_to": src.apply_to,
            "rule_count": len(src.rules),
            "rules": src.rules,
        }
        for src in sources
    ]


def _do_scan() -> None:
    """Run a full compliance scan and update _state."""
    _state.running = True
    _state.error = None
    try:
        results = run_checks(_SCAN_ROOT, all_sources=True)
        _state.results = _serialize_results(results)
        all_srcs = load_all_sources(_SCAN_ROOT)
        _state.constraints = _serialize_constraints(all_srcs)
        health = compute_rule_health([r.instruction for r in results], results)
        _state.health = _serialize_health(health)
        _state.health_summary = summarize(health)
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
    .tab-btn { color: #6b7280; border-bottom: 2px solid transparent; }
    .tab-btn.active { color: #1e40af; border-bottom-color: #1e40af; font-weight: 600; }
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
      <div class="flex items-center gap-2">
        <button id="key-btn"
          class="bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-2
                 rounded text-sm font-medium transition-colors flex items-center gap-2"
          title="Set the API key used to call the protected endpoints"
          onclick="setApiKey()">
          &#x1F511; API key
        </button>
        <button id="scan-btn"
          class="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2
                 rounded text-sm font-medium transition-colors flex items-center gap-2"
          onclick="triggerScan()">
          Run Scan
        </button>
      </div>
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

    <!-- Tab navigation -->
    <div id="tab-nav" class="hidden border-b border-gray-200 flex gap-6">
      <button class="tab-btn active pb-2 text-sm transition-colors"
        data-tab="violations" onclick="switchTab('violations')">Violations</button>
      <button class="tab-btn pb-2 text-sm transition-colors"
        data-tab="constraints" onclick="switchTab('constraints')">
        Constraints &nbsp;<span id="badge-constraints"
          class="bg-gray-100 text-gray-600 text-xs px-1.5 py-0.5 rounded-full"></span>
      </button>
    </div>

    <!-- ─── Violations tab ──────────────────────────────────────────────────── -->
    <div id="tab-violations">
      <!-- Filters + Search -->
      <div id="controls" class="flex flex-wrap gap-2 items-center">
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
      <div id="violations-container" class="space-y-3"></div>

      <!-- All clear -->
      <div id="all-clear" class="hidden text-center py-14">
        <div class="text-6xl mb-3">&#x2705;</div>
        <p class="text-green-600 font-semibold text-lg">All guidelines satisfied</p>
        <p class="text-gray-400 text-sm mt-1">No violations found in current filter.</p>
      </div>
    </div>

    <!-- ─── Constraints tab ─────────────────────────────────────────────────── -->
    <div id="tab-constraints" class="hidden space-y-4">
      <div class="flex items-center gap-3">
        <input id="cst-search" type="text" placeholder="Search constraints&hellip;"
          class="border border-gray-300 rounded px-3 py-1.5 text-sm
                 focus:outline-none focus:ring-2 focus:ring-purple-300 w-64"
          oninput="renderConstraints()">
        <span id="cst-summary" class="text-xs text-gray-500 ml-auto"></span>
      </div>
      <div id="constraints-container" class="space-y-4"></div>
      <div id="cst-empty" class="hidden text-center py-14 text-gray-400 text-sm">
        No constraint sources found. Add <code>.github/instructions/*.instructions.md</code>,
        <code>.github/copilot-instructions.md</code>, <code>CLAUDE.md</code>
        or <code>AGENTS.md</code> to your project.
      </div>
    </div>

  </main>

  <script>
    /* The API key is supplied by the user at runtime and kept only in this
       browser's sessionStorage — it is never embedded in the served HTML. */
    var _apiKey = '';
    try { _apiKey = sessionStorage.getItem('gc_api_key') || ''; } catch (_) {}

    function apiHeaders() {
      return _apiKey ? {'X-Api-Key': _apiKey} : {};
    }

    function setApiKey() {
      var k = window.prompt('Enter API key (leave blank if auth is disabled):', _apiKey || '');
      if (k === null) return false;            // user cancelled
      _apiKey = k.trim();
      try { sessionStorage.setItem('gc_api_key', _apiKey); } catch (_) {}
      return true;
    }

    /* Fetch wrapper: injects the API key header and, on 401/403, prompts the
       user for a key once and retries the request. */
    async function apiFetch(url, opts) {
      opts = opts || {};
      opts.headers = Object.assign({}, opts.headers || {}, apiHeaders());
      var resp = await fetch(url, opts);
      if ((resp.status === 401 || resp.status === 403) && setApiKey()) {
        opts.headers = Object.assign({}, opts.headers || {}, apiHeaders());
        resp = await fetch(url, opts);
      }
      return resp;
    }

    var _filter = 'all';
    var _data   = null;
    var _cdata  = null;
    var _activeTab = 'violations';

    /* ── helpers ─────────────────────────────────────────────────────────── */

    function esc(s) {
      return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    var _SOURCE_BADGE = {
      'copilot-instruction': 'bg-blue-100 text-blue-700',
      'copilot':             'bg-indigo-100 text-indigo-700',
      'claude':              'bg-purple-100 text-purple-700',
      'agents':              'bg-green-100 text-green-700',
    };

    function sourceBadge(type) {
      var cls = _SOURCE_BADGE[type] || 'bg-gray-100 text-gray-600';
      var label = type === 'copilot-instruction' ? 'copilot .instructions' : type;
      return '<span class="' + cls + ' px-2 py-0.5 rounded text-xs font-medium">' + esc(label) + '</span>';
    }

    /* ── tab switching ───────────────────────────────────────────────────── */

    function switchTab(tab) {
      _activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.tab === tab);
      });
      document.getElementById('tab-violations').classList.toggle('hidden', tab !== 'violations');
      document.getElementById('tab-constraints').classList.toggle('hidden', tab !== 'constraints');
      if (tab === 'constraints') renderConstraints();
    }

    /* ── violations tab ──────────────────────────────────────────────────── */

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
        container.innerHTML = '';
        allClear.classList.remove('hidden');
        return;
      }

      allClear.classList.add('hidden');
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

    /* ── constraints tab ─────────────────────────────────────────────────── */

    function renderConstraints() {
      if (!_cdata) return;
      var search = (document.getElementById('cst-search').value || '').toLowerCase();
      var sources = (_cdata.sources || []).filter(function(s) {
        if (!search) return true;
        if (s.name.toLowerCase().indexOf(search) >= 0) return true;
        if (s.description.toLowerCase().indexOf(search) >= 0) return true;
        return s.rules.some(function(r) { return r.toLowerCase().indexOf(search) >= 0; });
      });

      var container = document.getElementById('constraints-container');
      var empty     = document.getElementById('cst-empty');

      if (sources.length === 0) {
        container.innerHTML = '';
        empty.classList.remove('hidden');
        return;
      }
      empty.classList.add('hidden');

      container.innerHTML = sources.map(function(s) {
        var matchedRules = search
          ? s.rules.filter(function(r) { return r.toLowerCase().indexOf(search) >= 0; })
          : s.rules;
        if (search && matchedRules.length === 0) return '';
        var rulesHtml = matchedRules.length > 0
          ? '<ul class="mt-3 space-y-1.5">' + matchedRules.map(function(r) {
              return '<li class="text-sm text-gray-700 flex items-start gap-2">'
                + '<span class="text-gray-300 mt-0.5 flex-shrink-0">&#9656;</span>'
                + '<span>' + esc(r) + '</span></li>';
            }).join('') + '</ul>'
          : '<p class="text-xs text-gray-400 mt-2 italic">No rules extracted.</p>';
        return '<div class="bg-white border border-gray-200 rounded-lg shadow-sm p-4">'
          + '<div class="flex items-start gap-2 flex-wrap">'
          + sourceBadge(s.source_type)
          + '<span class="font-medium text-gray-800 text-sm">' + esc(s.description) + '</span>'
          + '<span class="ml-auto text-xs text-gray-400">' + esc(s.name) + '</span>'
          + '</div>'
          + (s.apply_to && s.apply_to !== '**/*'
              ? '<div class="mt-1 text-xs text-gray-400">applies to: '
                + '<code class="bg-gray-100 px-1 rounded">'
                + esc(s.apply_to) + '</code></div>'
              : '')
          + '<div class="text-xs text-gray-500 mt-1">'
            + esc(matchedRules.length)
            + ' rule' + (matchedRules.length !== 1 ? 's' : '') + '</div>'
          + rulesHtml
          + '</div>';
      }).filter(Boolean).join('');
    }

    /* ── shared state update ─────────────────────────────────────────────── */

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

      if (data.results !== null && !data.running) {
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('tab-nav').classList.remove('hidden');
        updateStats(data);
        renderViolations();
      }

      var btn = document.getElementById('scan-btn');
      btn.disabled = data.running;
      btn.innerHTML = data.running
        ? '<span class="spinner"></span> Scanning&hellip;'
        : 'Run Scan';
    }

    async function loadConstraints() {
      try {
        var r = await apiFetch('/api/constraints');
        _cdata = await r.json();
        var total = _cdata.total_rules || 0;
        document.getElementById('badge-constraints').textContent = total;
        document.getElementById('cst-summary').textContent =
          _cdata.total_sources + ' source' + (_cdata.total_sources !== 1 ? 's' : '')
          + ' \u2014 ' + total + ' rule' + (total !== 1 ? 's' : '');
        if (_activeTab === 'constraints') renderConstraints();
      } catch(_) {}
    }

    async function load() {
      try {
        var r = await apiFetch('/api/results');
        render(await r.json());
        await loadConstraints();
      } catch(_) {}
    }

    async function triggerScan() {
      document.getElementById('scan-btn').disabled = true;
      await apiFetch('/api/scan', { method: 'POST' });
      poll();
    }

    function poll() {
      setTimeout(async function() {
        var r    = await apiFetch('/api/results');
        var data = await r.json();
        render(data);
        if (data.running) poll();
        else await loadConstraints();
      }, 1500);
    }

    load();
    setInterval(load, 60000);
  </script>
</body>
</html>"""


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.get("/health", response_model=dict[str, str])
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, response_model=None)
def dashboard() -> str:
    """Serve the interactive dashboard.

    The HTML is fully static and contains no secret: when ``api_key`` auth is
    active the browser prompts the user for the key and stores it client-side,
    so the server never leaks it to anonymous visitors of this public route.
    """
    return _DASHBOARD_HTML


@app.post("/api/scan", response_model=dict[str, str], dependencies=[Depends(require_auth)])
def trigger_scan(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Trigger a new compliance scan in the background."""
    if _state.running:
        return {"status": "already_running"}
    background_tasks.add_task(_do_scan)
    return {"status": "started"}


@app.get("/api/results", response_model=None, dependencies=[Depends(require_auth)])
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


@app.get("/api/rules-health", response_model=None, dependencies=[Depends(require_auth)])
def get_rules_health() -> JSONResponse:
    """Return per-rule detection health — the truth a green scan hides.

    A ``dead`` rule carries no detector and no recognised phrase, so it can never
    flag a violation however clean the report looks.
    """
    return JSONResponse(
        {
            "timestamp": _state.timestamp,
            "summary": _state.health_summary,
            "rules": _state.health,
            "total_rules": len(_state.health),
        }
    )


class _ProposeRequest(BaseModel):
    """Ask the workshop to propose a detector for a rule and prove it."""

    rule: str
    apply_to: str = "**/*"


class _ArmRequest(BaseModel):
    """Write a validated detector onto a referential rule (dry-run by default)."""

    rule_id: str
    forbid: list[str] = Field(default_factory=list)
    forbid_regex: list[str] = Field(default_factory=list)
    file_regex: list[str] = Field(default_factory=list)
    ast: list[str] = Field(default_factory=list)
    scan: list[str] = Field(default_factory=list)
    match_in_comments: bool = False
    dry_run: bool = True


def _ollama_enabled() -> bool:
    """The LLM proposer is opt-in — off unless GC_OLLAMA is truthy."""
    return os.environ.get("GC_OLLAMA", "").lower() in {"1", "true", "yes"}


def _propose(rule: str, apply_to: str) -> Proposal | None:
    """Try the free heuristic first; escalate to Ollama only when enabled."""
    proposal = HeuristicProposer().propose(rule, apply_to)
    if proposal is None and _ollama_enabled():
        proposal = OllamaProposer(
            model=os.environ.get("GC_OLLAMA_MODEL", "qwen2.5:7b"),
            host=os.environ.get("GC_OLLAMA_HOST", "http://localhost:11434"),
        ).propose(rule, apply_to)
    return proposal


@app.post("/api/propose", response_model=None, dependencies=[Depends(require_auth)])
def propose_detector(req: _ProposeRequest) -> JSONResponse:
    """Propose a detector for a rule and replay it in the sandbox for proof.

    Returns ``proposal: null`` when the deterministic heuristic cannot map the
    rule's prose (e.g. the ``ai-models/`` rules) — the signal to escalate to an
    LLM backend. The LLM never judges: any proposal is proven here before a write.
    """
    proposal = _propose(req.rule, req.apply_to)
    if proposal is None:
        note = (
            "No proposal: the LLM backend could not map this rule mechanically."
            if _ollama_enabled()
            else "No deterministic proposal; enable the Ollama backend (GC_OLLAMA=1) to try an LLM."
        )
        return JSONResponse({"rule": req.rule, "proposal": None, "proof": None, "note": note})
    proof = replay(req.rule, proposal.detector, _SCAN_ROOT, req.apply_to)
    return JSONResponse(
        {
            "rule": req.rule,
            "proposal": {
                "source": proposal.source,
                "rationale": proposal.rationale,
                "forbid": list(proposal.detector.forbid),
                "match_in_comments": proposal.detector.match_in_comments,
            },
            "proof": {
                "match_count": proof.match_count,
                "files_scanned": proof.files_scanned,
                "hits": [{"file": h.file, "line": h.line, "excerpt": h.excerpt} for h in proof.hits],
            },
        }
    )


@app.post("/api/rules/detector", response_model=None, dependencies=[Depends(require_auth)])
def arm_rule(req: _ArmRequest) -> JSONResponse:
    """Write a validated detector onto a referential rule; ``dry_run`` shows the diff only."""
    detector = RuleDetector(
        forbid=tuple(req.forbid),
        forbid_regex=tuple(req.forbid_regex),
        file_regex=tuple(req.file_regex),
        ast_checks=tuple(req.ast),
        scan_checks=tuple(req.scan),
        match_in_comments=req.match_in_comments,
    )
    try:
        result = apply_detector(_SCAN_ROOT, req.rule_id, detector, dry_run=req.dry_run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(
        {
            "rule_id": result.rule_id,
            "file": str(result.file),
            "diff": result.diff,
            "written": result.written,
        }
    )


@app.get("/api/constraints", response_model=None, dependencies=[Depends(require_auth)])
def get_constraints() -> JSONResponse:
    """Return all extracted constraints from every discovered instruction source."""
    return JSONResponse(
        {
            "timestamp": _state.timestamp,
            "sources": _state.constraints,
            "total_rules": sum(s["rule_count"] for s in _state.constraints),
            "total_sources": len(_state.constraints),
        }
    )
