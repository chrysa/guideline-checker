"""Workshop web routes — propose/prove/persist a detector from the UI (spec §3.1, [workshop])."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from guideline_checker.core.health import replay
from guideline_checker.loader import RuleDetector
from guideline_checker.web.app import _active_root, _claude_available, _llm_enabled, _state, _truthy
from guideline_checker.web.auth import require_auth
from guideline_checker.workshop.interpret import interpret_rules
from guideline_checker.workshop.persist import apply_detector, find_rule_id_for_text, write_derived_ruleset
from guideline_checker.workshop.proposer import (
    ClaudeProposer,
    HeuristicProposer,
    OllamaProposer,
    Proposal,
    Proposer,
)

router = APIRouter()


def _do_interpret() -> None:
    """Interpret the active project's advisory prose into a proven, kinded ruleset.

    ADR D-0016 interpret-once: read the host prose the last scan surfaced as
    ``advisory`` (unenforced), propose a detector for each (heuristic → LLM),
    classify its kind, and sandbox-prove it. Writes nothing — the result is the
    per-repo derived ruleset offered for review, each rule kept only if a
    detector could be derived.
    """
    _state.interpret_running = True
    try:
        root = _active_root()
        advisory = [r["rule"] for r in _state.health if r.get("state") == "advisory"]
        derived = interpret_rules(
            advisory,
            propose=lambda rule: _propose(rule, "**/*"),
            replay=lambda rule, det: replay(rule, det, root, "**/*").match_count,
        )
        _state.derived_rules = derived  # kept for persist; the dicts below are for the API
        _state.derived = [
            {
                "rule": d.rule,
                "kind": d.kind,
                "match_count": d.match_count,
                "source": d.source,
                "patterns": list(d.detector.forbid) + [r + " (regex)" for r in d.detector.forbid_regex],
            }
            for d in derived
        ]
        _state.interpret_timestamp = datetime.now(UTC).isoformat()
    finally:
        _state.interpret_running = False


@router.post("/api/interpret", response_model=dict[str, str], dependencies=[Depends(require_auth)])
async def trigger_interpret(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Interpret the active project's advisory prose into a derived, proven ruleset."""
    if _state.interpret_running:
        return {"status": "already_running"}
    background_tasks.add_task(_do_interpret)
    return {"status": "started"}


@router.get("/api/interpret", response_model=None, dependencies=[Depends(require_auth)])
async def get_interpret() -> JSONResponse:
    """Return the last interpret-once result: the kinded, proven derived ruleset."""
    return JSONResponse(
        {
            "timestamp": _state.interpret_timestamp,
            "running": _state.interpret_running,
            "derived": _state.derived,
        }
    )


class _PersistRequest(BaseModel):
    """Write the last interpret-once ruleset into the per-repo derived cache."""

    dry_run: bool = True  # preview the diff; set false to write guidelines/derived/derived.yml


@router.post("/api/interpret/persist", response_model=None, dependencies=[Depends(require_auth)])
def persist_derived(req: _PersistRequest) -> JSONResponse:
    """Cache the derived ruleset (ADR D-0016): write it to guidelines/derived/derived.yml.

    Turns the last interpret-once result into the per-repo derived cache CI applies
    cold — the LLM has already proposed and the sandbox already proved; this only
    writes. ``dry_run`` (default) returns the diff and touches nothing.
    """
    if not _state.derived_rules:
        return JSONResponse({"written": False, "count": 0, "note": "Nothing to persist — run interpret first."})
    result = write_derived_ruleset(_active_root(), _state.derived_rules, dry_run=req.dry_run)
    return JSONResponse(
        {"file": str(result.file), "count": len(_state.derived_rules), "diff": result.diff, "written": result.written}
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


def _llm_proposer() -> Proposer | None:
    """Pick the backend: Claude (auto when installed, or GC_CLAUDE) preferred, else Ollama."""
    if _truthy("GC_CLAUDE") or _claude_available():
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


@router.post("/api/propose", response_model=None, dependencies=[Depends(require_auth)])
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
                "forbid_regex": list(proposal.detector.forbid_regex),
                "file_regex": list(proposal.detector.file_regex),
                "match_in_comments": proposal.detector.match_in_comments,
            },
            "proof": {
                "match_count": proof.match_count,
                "files_scanned": proof.files_scanned,
                "hits": [{"file": h.file, "line": h.line, "excerpt": h.excerpt} for h in proof.hits],
            },
        }
    )


@router.post("/api/rules/detector", response_model=None, dependencies=[Depends(require_auth)])
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


@router.post("/api/rules/resolve", response_model=None, dependencies=[Depends(require_auth)])
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

    try:
        proof = replay(req.rule, proposal.detector, _active_root(), req.apply_to)
    except re.error as exc:
        # A non-deterministic LLM can hand back a malformed regex; that is an
        # input error, not a server fault. Report it so the UI can re-propose.
        return JSONResponse(
            {"resolved": False, "armed": False, "reason": f"proposed detector is invalid ({exc}) — re-propose"}
        )
    payload: dict[str, Any] = {
        "resolved": True,
        "proposal": {
            "source": proposal.source,
            "rationale": proposal.rationale,
            "forbid": list(proposal.detector.forbid),
            "forbid_regex": list(proposal.detector.forbid_regex),
            "file_regex": list(proposal.detector.file_regex),
        },
        "proof": {
            "match_count": proof.match_count,
            "files_scanned": proof.files_scanned,
            "hits": [{"file": h.file, "line": h.line, "excerpt": h.excerpt} for h in proof.hits],
        },
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
