"""Web dashboard for guideline-checker results."""

from __future__ import annotations

import importlib.resources
import os
import threading
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from guideline_checker.checker import RuleResult, run_checks
from guideline_checker.loader import InstructionFile, RuleDetector, load_all_sources
from guideline_checker.persist import apply_detector, find_rule_id_for_text
from guideline_checker.proposer import (
    ClaudeProposer,
    HeuristicProposer,
    OllamaProposer,
    Proposal,
    Proposer,
)
from guideline_checker.rule_health import RuleHealth, compute_rule_health, summarize
from guideline_checker.sandbox import replay
from guideline_checker.web.auth import require_auth
from guideline_checker.workspace import discover_projects

# ── Configuration ──────────────────────────────────────────────────────────────

_SCAN_ROOT: Path = Path(os.environ.get("SCAN_ROOT", "."))
# Workspace = the directory holding sibling repos the workshop can switch between.
# Defaults to the scan root's parent so the fleet shows up with zero config; set
# GC_WORKSPACE to point elsewhere. Single-repo installs simply list one project.
_WORKSPACE: Path = Path(os.environ.get("GC_WORKSPACE") or _SCAN_ROOT.resolve().parent)


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
    """A rule is resolvable when a detector can plausibly be derived for it.

    Only ``dead`` and ``advisory`` rules need resolving (proven/armed already
    have a detector). Of those, one is actionable when the free heuristic maps
    its prose **or** an LLM backend is enabled to attempt it. Advisory rules are
    by construction the ones the phrase table cannot map, so with no LLM the
    honest answer is that they are not mechanically resolvable.
    """
    if entry.state.value not in {"dead", "advisory"}:
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
    """Optionally switch to another workspace project before scanning."""

    project: str | None = None  # a discovered project's name or absolute path


def _resolve_project(ident: str) -> str | None:
    """Map a project name/path to a discovered project path, or None (never trust raw input)."""
    for project in discover_projects(_WORKSPACE):
        if ident in (project.name, project.path):
            return project.path
    return None


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


@app.post("/api/scan", response_model=dict[str, str], dependencies=[Depends(require_auth)])
def trigger_scan(background_tasks: BackgroundTasks, req: _ScanRequest | None = None) -> dict[str, str]:
    """Trigger a scan in the background, optionally switching to another project first."""
    if req is not None and req.project:
        resolved = _resolve_project(req.project)
        if resolved is None:
            raise HTTPException(status_code=404, detail=f"Unknown project: {req.project!r}")
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


@app.post("/api/scan-all", response_model=dict[str, str], dependencies=[Depends(require_auth)])
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
    # ADR D-0016: the host prose sentence this detector was derived from.
    provenance: str = ""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _llm_enabled() -> bool:
    """The LLM escalation is opt-in — off unless a backend flag is set."""
    return _truthy("GC_CLAUDE") or _truthy("GC_OLLAMA")


def _llm_proposer() -> Proposer | None:
    """Pick the enabled LLM backend: Claude (portable) preferred, else Ollama (local)."""
    if _truthy("GC_CLAUDE"):
        return ClaudeProposer(binary=os.environ.get("GC_CLAUDE_BIN", "claude"))
    if _truthy("GC_OLLAMA"):
        return OllamaProposer(
            model=os.environ.get("GC_OLLAMA_MODEL", "qwen2.5:7b"),
            host=os.environ.get("GC_OLLAMA_HOST", "http://localhost:11434"),
        )
    return None


def _propose(rule: str, apply_to: str) -> Proposal | None:
    """Try the free heuristic first; escalate to the enabled LLM backend if any."""
    proposal = HeuristicProposer().propose(rule, apply_to)
    if proposal is None:
        backend = _llm_proposer()
        if backend is not None:
            proposal = backend.propose(rule, apply_to)
    return proposal


@app.post("/api/propose", response_model=None, dependencies=[Depends(require_auth)])
def propose_detector(req: _ProposeRequest) -> JSONResponse:
    """Propose a detector for a rule and replay it in the sandbox for proof.

    Returns ``proposal: null`` when the deterministic heuristic cannot map the
    rule's prose (semantic or provider-specific guidance) — the signal to escalate
    to an LLM backend. The LLM never judges: any proposal is proven before a write.
    """
    proposal = _propose(req.rule, req.apply_to)
    if proposal is None:
        note = (
            "No proposal: the LLM backend could not map this rule mechanically."
            if _llm_enabled()
            else "No deterministic proposal; enable an LLM backend (GC_CLAUDE=1 or GC_OLLAMA=1)."
        )
        return JSONResponse({"rule": req.rule, "proposal": None, "proof": None, "note": note})
    proof = replay(req.rule, proposal.detector, _active_root(), req.apply_to)
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
        result = apply_detector(
            _active_root(),
            req.rule_id,
            detector,
            dry_run=req.dry_run,
            provenance=req.provenance or None,
        )
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


class _ResolveRequest(BaseModel):
    """One-click resolution of a dead/advisory rule: propose → prove → arm."""

    rule: str
    rule_id: str | None = None  # required to write the detector (YAML rules only)
    apply_to: str = "**/*"
    dry_run: bool = True  # preview the diff; set false to actually write the detector


@app.post("/api/rules/resolve", response_model=None, dependencies=[Depends(require_auth)])
def resolve_rule(req: _ResolveRequest) -> JSONResponse:
    """Resolve a rule end to end: derive a detector, prove it, then arm it.

    Chains the existing steps (``_propose`` → ``replay`` → ``apply_detector``)
    into one action. Detection stays deterministic: the proposal is always
    replayed for proof, and a rule with no YAML id (a markdown-sourced advisory)
    is proposed-and-proven only — there is nowhere to persist it, so it is
    reported ``armed: false`` rather than silently dropped.
    """
    proposal = _propose(req.rule, req.apply_to)
    if proposal is None:
        return JSONResponse({"resolved": False, "armed": False, "reason": "no detector could be derived"})

    proof = replay(req.rule, proposal.detector, _active_root(), req.apply_to)
    payload: dict[str, Any] = {
        "resolved": True,
        "proposal": {
            "source": proposal.source,
            "rationale": proposal.rationale,
            "forbid": list(proposal.detector.forbid),
        },
        "proof": {"match_count": proof.match_count, "files_scanned": proof.files_scanned},
        "armed": False,
    }
    rule_id = req.rule_id or find_rule_id_for_text(_active_root(), req.rule)
    if rule_id:
        try:
            result = apply_detector(
                _active_root(), rule_id, proposal.detector, dry_run=req.dry_run, provenance=req.rule
            )
        except KeyError:
            payload["note"] = "No YAML rule with this id — proposed and proven only (markdown source)."
        else:
            payload.update(rule_id=rule_id, armed=True, written=result.written, diff=result.diff, file=str(result.file))
    else:
        payload["note"] = "Markdown-sourced rule — proposed and proven only (no YAML entry to write to)."
    return JSONResponse(payload)


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
