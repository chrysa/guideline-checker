"""Web dashboard for guideline-checker results."""

from __future__ import annotations

import importlib.resources
import os
import shutil
import threading
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from guideline_checker.core.detection import RuleResult, run_checks
from guideline_checker.core.health import RuleHealth, compute_rule_health, summarize
from guideline_checker.loader import InstructionFile, load_all_sources
from guideline_checker.web.auth import require_auth
from guideline_checker.web.mode import require_workshop
from guideline_checker.web.security_headers import SecurityHeadersMiddleware
from guideline_checker.workshop.interpret import DerivedRule
from guideline_checker.workshop.proposer import HeuristicProposer
from guideline_checker.workspace import discover_projects, has_rule_source

# ── Configuration ──────────────────────────────────────────────────────────────

_SCAN_ROOT: Path = Path(os.environ.get("SCAN_ROOT", "."))
# Workspace = the directory holding sibling repos the workshop can switch between.
# Defaults to the scan root's parent so the fleet shows up with zero config; set
# GC_WORKSPACE to point elsewhere. Single-repo installs simply list one project.
_WORKSPACE: Path = Path(os.environ.get("GC_WORKSPACE") or _SCAN_ROOT.resolve().parent)


# Directory-browser root: the user may pick any folder beneath it to scan, bounded by
# _within_base (no traversal above it). GC_BROWSE_ROOT sets it explicitly; unset, a
# native run defaults to the user's home so the Browse… dialog reaches any folder on
# the machine (a local, loopback dev tool). The container sets GC_BROWSE_ROOT to
# /workspace, so this home default never widens a container's view.
def _default_browse_root(env: str | None, home: Path) -> Path:
    """GC_BROWSE_ROOT if set, else the user's home directory."""
    return Path(env or home).resolve()


_BROWSE_ROOT: Path = _default_browse_root(os.environ.get("GC_BROWSE_ROOT"), Path.home())


def _active_root() -> Path:
    """The project the workshop currently targets (the picked one, else the default)."""
    return Path(_state.active_project) if _state.active_project else _SCAN_ROOT


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
    active_project: str | None = None
    timestamp: str | None = None
    running: bool = False
    error: str | None = None
    # Compliance grade for the active project (derived from scan violations).
    compliance: dict[str, Any] = field(default_factory=dict)
    # Fleet aggregate (the "All projects" view): one health summary per
    # discovered project, computed on demand by _do_scan_all.
    all_summaries: list[dict[str, Any]] = field(default_factory=list)
    all_timestamp: str | None = None
    all_running: bool = False
    # Interpret-once (ADR D-0016): the kinded, proven ruleset derived from the
    # active project's advisory prose, computed on demand by workshop.web_endpoints._do_interpret.
    derived: list[dict[str, Any]] = field(default_factory=list)
    derived_rules: list[DerivedRule] = field(default_factory=list)  # objects, for persist
    interpret_running: bool = False
    interpret_timestamp: str | None = None


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


# A single heuristic proposer answers the "can this rule be resolved?" question
# without any LLM: if it maps the rule's prose to a detector, the rule is one
# click away from being armed (see the /api/rules/resolve endpoint).
_HEURISTIC: HeuristicProposer = HeuristicProposer()


def _is_resolvable(entry: RuleHealth) -> bool:
    """A rule is resolvable when the workshop can arm it in one click.

    Only ``dead`` rules qualify: they are YAML referential rules advertised as
    enforceable but carrying no detector, so a proven detector can be written
    back onto them. (``advisory`` rules are markdown-sourced — a detector can be
    *proposed* for them in the panel, but there is no YAML entry to persist, so
    they are not one-click resolvable.) Actionable when the free heuristic maps
    the prose or an LLM backend is available to attempt it.
    """
    if entry.state.value != "dead":
        return False
    return _HEURISTIC.propose(entry.rule) is not None or _llm_enabled()


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
            "provenance": h.provenance,
            "kind": h.kind,
            "resolvable": _is_resolvable(h),
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


def _count_severities(results: list[RuleResult]) -> tuple[int, int, int]:
    """Total (errors, warnings, infos) across every violation of a scan."""
    counts: Counter[str] = Counter(v.severity for r in results for v in r.violations)
    return counts.get("error", 0), counts.get("warning", 0), counts.get("info", 0)


def _compliance_note(errors: int, warnings: int, dead: int, total_rules: int) -> dict[str, Any]:
    """Grade a project's compliance from its scan — a letter a human reads at a glance.

    Compliance is about the code obeying its rules, so error/warning violations
    dominate; a few dead referential rules shave a little off (the checker lied
    about being able to enforce them). ``n/a`` when there is nothing to grade.
    """
    if total_rules == 0:
        return {"grade": "n/a", "score": None, "errors": errors, "warnings": warnings, "dead": dead}
    score = 100
    score -= min(48, errors * 8)  # errors are blocking — heaviest, but bounded
    score -= min(20, warnings)  # warnings sting lightly and cap out
    score -= min(12, dead * 2)  # a rule that can't fire is a referential defect
    score = max(0, score)
    grade = next(g for threshold, g in _GRADE_BANDS if score >= threshold)
    return {"grade": grade, "score": score, "errors": errors, "warnings": warnings, "dead": dead}


_GRADE_BANDS: tuple[tuple[int, str], ...] = ((90, "A"), (75, "B"), (60, "C"), (45, "D"), (0, "F"))


def _do_scan() -> None:
    """Run a full compliance scan of the active project and update _state."""
    root = _active_root()
    _state.running = True
    _state.error = None
    try:
        results = run_checks(root, all_sources=True)
        _state.results = _serialize_results(results)
        all_srcs = load_all_sources(root)
        _state.constraints = _serialize_constraints(all_srcs)
        health = compute_rule_health([r.instruction for r in results], results)
        _state.health = _serialize_health(health)
        _state.health_summary = summarize(health)
        errors, warnings, _infos = _count_severities(results)
        _state.compliance = _compliance_note(errors, warnings, _state.health_summary.get("dead", 0), len(health))
        _state.timestamp = datetime.now(UTC).isoformat()
    except Exception as exc:  # pragma: no cover
        _state.error = str(exc)
    finally:
        _state.running = False


def _project_health_summary(root: Path) -> dict[str, Any]:
    """Scan one project and return only its health summary — the fleet-view unit.

    Deliberately light: no per-rule payload, just the state counts plus how many
    rules are resolvable, so the aggregate stays cheap across many repos.
    """
    results = run_checks(root, all_sources=True)
    health = compute_rule_health([r.instruction for r in results], results)
    summary = summarize(health)
    resolvable = sum(1 for h in health if _is_resolvable(h))
    errors, warnings, _infos = _count_severities(results)
    compliance = _compliance_note(errors, warnings, summary.get("dead", 0), len(health))
    return {"summary": summary, "total": len(health), "resolvable": resolvable, "compliance": compliance}


def _do_scan_all() -> None:
    """Compute a health summary for every discovered project (the fleet cockpit)."""
    _state.all_running = True
    try:
        summaries: list[dict[str, Any]] = []
        for project in discover_projects(_WORKSPACE):
            try:
                data = _project_health_summary(Path(project.path))
            except Exception as exc:  # pragma: no cover - one bad repo must not sink the fleet view
                summaries.append({"name": project.name, "path": project.path, "error": str(exc)})
                continue
            summaries.append({"name": project.name, "path": project.path, **data})
        _state.all_summaries = summaries
        _state.all_timestamp = datetime.now(UTC).isoformat()
    finally:
        _state.all_running = False


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _falsey(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"0", "false", "no"}


def _claude_available() -> bool:
    """The Claude CLI backend auto-enables in the workshop when it is installed.

    ADR D-0013 makes the Claude CLI the default LLM backend; requiring a flag
    made "propose a fix" silently do nothing out of the box. So when the CLI is
    on PATH we use it automatically — set ``GC_CLAUDE=0`` to opt out. This only
    affects the *workshop* proposal step; the CI/pre-commit gate never calls an
    LLM (the deterministic boundary of D-0012 is untouched).
    """
    if _falsey("GC_CLAUDE"):
        return False
    return shutil.which(os.environ.get("GC_CLAUDE_BIN", "claude")) is not None


def _llm_enabled() -> bool:
    """True when a proposal backend is available (auto Claude CLI, or an opt-in flag)."""
    return _truthy("GC_CLAUDE") or _truthy("GC_OLLAMA") or _claude_available()


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
app.add_middleware(SecurityHeadersMiddleware)

# The propose/interpret/persist router is an optional plugin: this try/except means
# a missing workshop.web_endpoints module gracefully drops the panel's routes rather than
# crashing the dashboard. However, web/app.py has unconditional imports from workshop/
# at the top (HeuristicProposer, DerivedRule) used by the _is_resolvable check and state;
# those are not guarded by this block, so a broader workshop import failure would still crash.
_workshop_router: APIRouter | None = None
try:
    from guideline_checker.workshop.web_endpoints import router as _imported_workshop_router
except ImportError:
    pass
else:
    _workshop_router = _imported_workshop_router

if _workshop_router is not None:
    app.include_router(_workshop_router)

# ── Dashboard HTML ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _dashboard_html() -> str:
    """Load the single-page workshop UI from the bundled static asset (ADR D-0011)."""
    return (importlib.resources.files("guideline_checker.web") / "static" / "index.html").read_text(encoding="utf-8")


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
    return _dashboard_html()


class _ScanRequest(BaseModel):
    """Optionally switch to another target before scanning."""

    project: str | None = None  # a discovered project's name or absolute path
    path: str | None = None  # an arbitrary directory under the browse root (folder browser)


def _resolve_project(ident: str) -> str | None:
    """Map a project name/path to a discovered project path, or None (never trust raw input)."""
    for project in discover_projects(_WORKSPACE):
        if ident in (project.name, project.path):
            return project.path
    return None


def _within_base(base: Path, candidate: str | Path) -> Path | None:
    """Resolve ``candidate`` under ``base`` and return it only if it stays inside — else None.

    A relative candidate resolves against ``base``; an absolute one is taken as given. The
    resolved path must equal ``base`` or live beneath it, which is what stops ``..`` and
    symlink traversal from reaching arbitrary host directories.
    """
    raw = Path(candidate)
    target = (raw if raw.is_absolute() else base / raw).resolve()
    if target == base or base in target.parents:
        return target
    return None


def _browse_listing(base: Path, path: str | None) -> dict[str, Any]:
    """List the sub-directories of ``path`` (default ``base``), bounded to ``base``.

    Returns the current directory, its parent (``None`` at the root so the UI cannot
    climb out), whether it carries rule sources, and its visible sub-directories.
    """
    cwd = _within_base(base, path) if path else base
    if cwd is None or not cwd.is_dir():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path outside the browse root")
    entries = [
        {"name": child.name, "path": str(child)}
        for child in sorted(cwd.iterdir())
        if child.is_dir() and not child.name.startswith(".")
    ]
    return {
        "base": str(base),
        "cwd": str(cwd),
        "parent": None if cwd == base else str(cwd.parent),
        "scannable": has_rule_source(cwd),
        "entries": entries,
    }


@app.get("/api/projects", response_model=None, dependencies=[Depends(require_auth)])
def get_projects() -> JSONResponse:
    """List the workspace projects the workshop can scan, and which one is active."""
    projects = discover_projects(_WORKSPACE)
    active = _state.active_project or str(_SCAN_ROOT.resolve())
    return JSONResponse(
        {
            "workspace": str(_WORKSPACE),
            "active": active,
            "projects": [{"name": p.name, "path": p.path} for p in projects],
        }
    )


@app.get("/api/browse", response_model=None, dependencies=[Depends(require_auth)])
def browse_dirs(path: str | None = None) -> JSONResponse:
    """List sub-directories under the browse root so the UI can pick any folder to scan."""
    return JSONResponse(_browse_listing(_BROWSE_ROOT, path))


@app.post("/api/scan", response_model=dict[str, str], dependencies=[Depends(require_auth), Depends(require_workshop)])
def trigger_scan(background_tasks: BackgroundTasks, req: _ScanRequest | None = None) -> dict[str, str]:
    """Trigger a scan in the background, optionally switching to another target first.

    A ``path`` (folder browser) is honoured over a ``project`` (discovered-repo dropdown);
    both are validated server-side so a scan never reaches outside the allowed roots.
    """
    if req is not None and req.path:
        resolved_path = _within_base(_BROWSE_ROOT, req.path)
        if resolved_path is None or not resolved_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path outside the browse root: {req.path!r}",
            )
        _state.active_project = str(resolved_path)
    elif req is not None and req.project:
        resolved = _resolve_project(req.project)
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown project: {req.project!r}")
        _state.active_project = resolved
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
    resolvable = sum(1 for r in _state.health if r.get("resolvable"))
    return JSONResponse(
        {
            "timestamp": _state.timestamp,
            "summary": _state.health_summary,
            "resolvable": resolvable,
            "compliance": _state.compliance,
            "rules": _state.health,
            "total_rules": len(_state.health),
        }
    )


@app.post(
    "/api/scan-all",
    response_model=dict[str, str],
    dependencies=[Depends(require_auth), Depends(require_workshop)],
)
def trigger_scan_all(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Compute a health summary for every workspace project (the fleet cockpit)."""
    if _state.all_running:
        return {"status": "already_running"}
    background_tasks.add_task(_do_scan_all)
    return {"status": "started"}


@app.get("/api/health-all", response_model=None, dependencies=[Depends(require_auth)])
def get_health_all() -> JSONResponse:
    """Return the per-project health summaries from the last fleet scan."""
    combined: dict[str, int] = {}
    errors = warnings = dead = total = 0
    for entry in _state.all_summaries:
        for state_name, count in (entry.get("summary") or {}).items():
            combined[state_name] = combined.get(state_name, 0) + count
        note = entry.get("compliance") or {}
        errors += note.get("errors", 0)
        warnings += note.get("warnings", 0)
        dead += note.get("dead", 0)
        total += entry.get("total", 0)
    return JSONResponse(
        {
            "timestamp": _state.all_timestamp,
            "running": _state.all_running,
            "projects": _state.all_summaries,
            "combined": combined,
            "compliance": _compliance_note(errors, warnings, dead, total),
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
