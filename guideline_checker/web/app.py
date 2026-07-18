"""Web dashboard for guideline-checker results."""

from __future__ import annotations

import importlib.resources
import os
import threading
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
from guideline_checker.persist import apply_detector
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
    rule's prose (e.g. the ``ai-models/`` rules) — the signal to escalate to an
    LLM backend. The LLM never judges: any proposal is proven here before a write.
    """
    proposal = _propose(req.rule, req.apply_to)
    if proposal is None:
        note = (
            "No proposal: the LLM backend could not map this rule mechanically."
            if _llm_enabled()
            else "No deterministic proposal; enable an LLM backend (GC_CLAUDE=1 or GC_OLLAMA=1)."
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
