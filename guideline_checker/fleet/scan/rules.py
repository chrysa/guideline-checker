"""Closed registry of evaluation methods — one evaluator per ``method.type`` of the SPEC.

Each evaluator returns an :class:`Outcome` (status + message + evidence). The contract:
* evidence that could not be determined (no CI run, truncated tree, file too large) → ``unknown``;
* a collection failure behind the evidence → ``error`` with the typed kind;
* ``pass`` is only ever produced from positive evidence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from guideline_checker.fleet.scan.collect import (
    FileContent,
    Provenance,
    RepoEvidence,
    TreeSnapshot,
    parse_github_datetime,
)
from guideline_checker.fleet.scan.globbing import glob_matches
from guideline_checker.fleet.scan.models import (
    METHOD_TYPES,
    CheckResult,
    CiLastRunMethod,
    CiLastRunParams,
    CollectionError,
    CommitMessagesAbsentMethod,
    ErrorKind,
    Evidence,
    FileAbsentMethod,
    FileExistsMethod,
    FileRegexMethod,
    LastCommitAgeMethod,
    MakefileTargetsMethod,
    RepoFieldMethod,
    RuleDef,
    Status,
    TreeGlobMethod,
    json_safe,
)
from guideline_checker.fleet.scan.profiles import Detection, ProfileResolution

MAX_EVIDENCE_ITEMS = 10
_SUCCESS = "success"
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.%/-]+(?:[ \t]+[A-Za-z0-9_.%/-]+)*)[ \t]*::?(?!=)", re.MULTILINE)


@dataclass(frozen=True)
class Outcome:
    status: Status
    message: str
    evidence: list[Evidence] = field(default_factory=list)
    error_kind: ErrorKind | None = None


@dataclass(frozen=True)
class RuleContext:
    evidence: RepoEvidence
    now: datetime


def _ev(*, kind: str, locator: str, value: Any, provenance: Provenance) -> Evidence:  # noqa: ANN401
    return Evidence(
        kind=kind,
        locator=locator,
        value=json_safe(value),
        source_endpoint=provenance.endpoint,
        fetched_at=provenance.fetched_at,
    )


def _from_error(error: CollectionError, *, what: str) -> Outcome:
    return Outcome(status=Status.ERROR, message=f"{what}: {error.message}", error_kind=error.kind)


def _missing_facet(ctx: RuleContext, *, facets: tuple[str, ...], what: str) -> Outcome:
    """The facet is absent: ``error`` if a collection error explains it, ``unknown`` otherwise."""
    for facet in facets:
        error = ctx.evidence.errors.get(facet)
        if error is not None:
            return _from_error(error, what=f"{what} not collected")
    return Outcome(status=Status.UNKNOWN, message=f"{what} not available")


def _tree_or_outcome(ctx: RuleContext) -> TreeSnapshot | Outcome:
    tree = ctx.evidence.tree
    if tree is None:
        return _missing_facet(ctx, facets=("tree", "head", "metadata"), what="git tree")
    return tree


# ─── Tree-based methods ───────────────────────────────────────────────────────


def eval_file_exists(method: FileExistsMethod, ctx: RuleContext) -> Outcome:
    tree = _tree_or_outcome(ctx)
    if isinstance(tree, Outcome):
        return tree
    found = [p for p in method.params.any_of if tree.has(p)]
    if found:
        evidence = [_ev(kind="tree_path", locator=found[0], value=True, provenance=tree.provenance)]
        return Outcome(status=Status.PASS, message=f"{found[0]} present", evidence=evidence)
    if tree.truncated:
        return Outcome(status=Status.UNKNOWN, message="tree truncated; absence cannot be proven")
    listed = ", ".join(method.params.any_of)
    evidence = [_ev(kind="tree_path", locator=listed, value=False, provenance=tree.provenance)]
    return Outcome(status=Status.FAIL, message=f"none of [{listed}] present", evidence=evidence)


def eval_file_absent(method: FileAbsentMethod, ctx: RuleContext) -> Outcome:
    tree = _tree_or_outcome(ctx)
    if isinstance(tree, Outcome):
        return tree
    paths = tree.all_paths
    matches = sorted({m for pattern in method.params.paths for m in glob_matches(pattern=pattern, paths=paths)})
    if matches:
        evidence = [
            _ev(kind="tree_path", locator=m, value=True, provenance=tree.provenance)
            for m in matches[:MAX_EVIDENCE_ITEMS]
        ]
        return Outcome(
            status=Status.FAIL, message=f"{len(matches)} forbidden path(s), e.g. {matches[0]}", evidence=evidence
        )
    if tree.truncated:
        return Outcome(status=Status.UNKNOWN, message="tree truncated; absence cannot be proven")
    listed = ", ".join(method.params.paths)
    evidence = [_ev(kind="tree_glob", locator=listed, value=0, provenance=tree.provenance)]
    return Outcome(status=Status.PASS, message=f"no path matches [{listed}]", evidence=evidence)


def eval_tree_glob(method: TreeGlobMethod, ctx: RuleContext) -> Outcome:
    tree = _tree_or_outcome(ctx)
    if isinstance(tree, Outcome):
        return tree
    params = method.params
    matches = glob_matches(pattern=params.glob, paths=tree.all_paths)
    evidence = [_ev(kind="tree_glob", locator=params.glob, value=len(matches), provenance=tree.provenance)]
    if len(matches) >= params.min_count:
        return Outcome(status=Status.PASS, message=f"{len(matches)} path(s) match {params.glob}", evidence=evidence)
    if tree.truncated:
        return Outcome(status=Status.UNKNOWN, message="tree truncated; count may be incomplete", evidence=evidence)
    message = f"{len(matches)} path(s) match {params.glob}, need {params.min_count}"
    return Outcome(status=Status.FAIL, message=message, evidence=evidence)


# ─── Content-based methods ────────────────────────────────────────────────────


def _first_file(ctx: RuleContext, *, candidates: list[str]) -> FileContent | Outcome | None:
    """The first present candidate, an Outcome when undeterminable, or ``None`` when all are missing."""
    states = [ctx.evidence.files.get(path) for path in candidates]
    for state in states:
        if state is not None and state.state == "present":
            return state
    if any(s is None for s in states):
        return _missing_facet(ctx, facets=("tree", "head", "metadata"), what="file content")
    for state in states:
        if state is not None and state.state == "error" and state.error is not None:
            return _from_error(state.error, what=f"{state.path} unreadable")
    too_large = [s.path for s in states if s is not None and s.state == "too_large"]
    if too_large:
        return Outcome(status=Status.UNKNOWN, message=f"{too_large[0]} exceeds the size cap; not inspected")
    return None


def eval_file_regex(method: FileRegexMethod, ctx: RuleContext) -> Outcome:
    params = method.params
    found = _first_file(ctx, candidates=params.candidates)
    if isinstance(found, Outcome):
        return found
    if found is None:
        policy = params.missing or ("fail" if params.expect == "present" else "pass")
        status = {"fail": Status.FAIL, "pass": Status.PASS, "unknown": Status.UNKNOWN}[policy]
        return Outcome(status=status, message=f"file missing: {', '.join(params.candidates)} (missing → {policy})")
    match = re.search(params.pattern, found.content or "", flags=re.MULTILINE)
    provenance = found.provenance or Provenance(endpoint="", fetched_at=ctx.now)
    snippet = match.group(0)[:120] if match else None
    evidence = [_ev(kind="file_content", locator=found.path, value=snippet, provenance=provenance)]
    if (match is not None) == (params.expect == "present"):
        return Outcome(
            status=Status.PASS, message=f"{found.path}: pattern {params.expect} as expected", evidence=evidence
        )
    verdict = "found" if match else "not found"
    return Outcome(status=Status.FAIL, message=f"{found.path}: pattern {verdict}", evidence=evidence)


def declared_make_targets(content: str) -> set[str]:
    """Target names declared in a Makefile (static parse; variables are not expanded)."""
    return {name for line in _MAKE_TARGET.findall(content) for name in line.split()}


def eval_makefile_targets(method: MakefileTargetsMethod, ctx: RuleContext) -> Outcome:
    params = method.params
    found = _first_file(ctx, candidates=[params.path])
    if isinstance(found, Outcome):
        return found
    if found is None:
        return Outcome(status=Status.FAIL, message=f"{params.path} missing")
    declared = declared_make_targets(found.content or "")
    missing = [t for t in params.targets if t not in declared]
    provenance = found.provenance or Provenance(endpoint="", fetched_at=ctx.now)
    evidence = [_ev(kind="make_targets", locator=params.path, value=sorted(declared), provenance=provenance)]
    if missing:
        return Outcome(status=Status.FAIL, message=f"missing target(s): {', '.join(missing)}", evidence=evidence)
    return Outcome(status=Status.PASS, message=f"all {len(params.targets)} target(s) declared", evidence=evidence)


# ─── Metadata / history methods ───────────────────────────────────────────────


def _repo_field_value(metadata: dict[str, Any], field_name: str) -> tuple[bool, Any]:
    if field_name == "license":
        license_info = metadata.get("license")
        return True, (license_info or {}).get("spdx_id") if isinstance(license_info, dict) else None
    if field_name not in metadata:
        return False, None
    return True, metadata[field_name]


def eval_repo_field(method: RepoFieldMethod, ctx: RuleContext) -> Outcome:
    metadata = ctx.evidence.metadata
    provenance = ctx.evidence.metadata_provenance
    if metadata is None or provenance is None:
        return _missing_facet(ctx, facets=("metadata",), what="repo metadata")
    params = method.params
    present, actual = _repo_field_value(metadata, params.field)
    if not present:
        return Outcome(status=Status.UNKNOWN, message=f"field {params.field} not reported by the API")
    expected = params.value
    holds = {
        "eq": actual == expected,
        "ne": actual != expected,
        "in": isinstance(expected, list) and actual in expected,
    }[params.op]
    evidence = [_ev(kind="repo_field", locator=params.field, value=actual, provenance=provenance)]
    status = Status.PASS if holds else Status.FAIL
    return Outcome(status=status, message=f"{params.field}={actual!r} {params.op} {expected!r}", evidence=evidence)


def eval_ci_last_run(method: CiLastRunMethod, ctx: RuleContext) -> Outcome:
    params = method.params
    run_info = ctx.evidence.ci_runs.get(params.branch)
    if run_info is None:
        return _missing_facet(ctx, facets=(f"ci:{params.branch}", "metadata"), what="workflow runs")
    run = run_info.run
    if run is None:
        return Outcome(status=Status.UNKNOWN, message=f"no completed workflow run on {run_info.branch}")
    conclusion = run.get("conclusion")
    stamp = run.get("updated_at") or run.get("created_at")
    value = {
        "id": run.get("id"),
        "conclusion": conclusion,
        "event": run.get("event"),
        "run_started_at": run.get("run_started_at"),
        "updated_at": stamp,
        "name": run.get("name"),
    }
    evidence = [_ev(kind="workflow_run", locator=run_info.branch, value=value, provenance=run_info.provenance)]
    if conclusion != _SUCCESS:
        return _failed_run_outcome(run=run, params=params, evidence=evidence)
    if params.max_age_days is not None:
        when = parse_github_datetime(stamp)
        if when is None:
            return Outcome(status=Status.UNKNOWN, message="run date unreadable", evidence=evidence)
        if ctx.now - when > timedelta(days=params.max_age_days):
            return Outcome(
                status=Status.FAIL,
                message=f"latest successful run older than {params.max_age_days}d",
                evidence=evidence,
            )
    return Outcome(status=Status.PASS, message="latest completed run succeeded", evidence=evidence)


def _failed_run_outcome(*, run: dict[str, Any], params: CiLastRunParams, evidence: list[Evidence]) -> Outcome:
    """``fail`` for a real failure; ``unknown`` when the run died before any step could run."""
    duration = _run_duration_s(run)
    if params.min_failure_duration_s and duration is not None and duration < params.min_failure_duration_s:
        return Outcome(
            status=Status.UNKNOWN,
            message=f"latest run failed after {duration}s: no step ran (account/billing block?), not a code verdict",
            evidence=evidence,
        )
    return Outcome(status=Status.FAIL, message=f"latest run concluded {run.get('conclusion')!r}", evidence=evidence)


def _run_duration_s(run: dict[str, Any]) -> int | None:
    """Seconds between a run's start and its last update, or ``None`` when either is unreadable."""
    started = parse_github_datetime(run.get("run_started_at"))
    ended = parse_github_datetime(run.get("updated_at"))
    if started is None or ended is None:
        return None
    return int((ended - started).total_seconds())


def eval_commit_messages_absent(method: CommitMessagesAbsentMethod, ctx: RuleContext) -> Outcome:
    commits = ctx.evidence.commits
    if commits is None:
        return _missing_facet(ctx, facets=("commits", "head", "metadata"), what="recent commits")
    params = method.params
    pattern = re.compile(params.pattern)
    window = commits.commits[: params.last_n]
    hits = [c for c in window if pattern.search(c.message)]
    if hits:
        evidence = [
            _ev(
                kind="commit",
                locator=c.sha,
                value=c.message.splitlines()[0][:120] if c.message else "",
                provenance=commits.provenance,
            )
            for c in hits[:MAX_EVIDENCE_ITEMS]
        ]
        return Outcome(
            status=Status.FAIL, message=f"{len(hits)}/{len(window)} recent commit(s) match", evidence=evidence
        )
    evidence = [_ev(kind="commit_window", locator=params.pattern, value=len(window), provenance=commits.provenance)]
    return Outcome(status=Status.PASS, message=f"none of {len(window)} recent commit(s) match", evidence=evidence)


def eval_last_commit_age(method: LastCommitAgeMethod, ctx: RuleContext) -> Outcome:
    head = ctx.evidence.head
    if head is None:
        return _missing_facet(ctx, facets=("head", "metadata"), what="head commit")
    if head.committed_at is None:
        return Outcome(status=Status.UNKNOWN, message="head commit date unreadable")
    age_days = (ctx.now - head.committed_at).days
    evidence = [
        _ev(kind="head_commit", locator=head.sha, value=head.committed_at.isoformat(), provenance=head.provenance)
    ]
    status = Status.PASS if age_days <= method.params.max_days else Status.FAIL
    return Outcome(
        status=status, message=f"head commit {age_days}d old (max {method.params.max_days}d)", evidence=evidence
    )


Evaluator = Callable[[Any, RuleContext], Outcome]

REGISTRY: dict[str, Evaluator] = {
    "file_exists": eval_file_exists,
    "file_absent": eval_file_absent,
    "tree_glob": eval_tree_glob,
    "file_regex": eval_file_regex,
    "makefile_targets": eval_makefile_targets,
    "repo_field": eval_repo_field,
    "ci_last_run": eval_ci_last_run,
    "commit_messages_absent": eval_commit_messages_absent,
    "last_commit_age": eval_last_commit_age,
}

if set(REGISTRY) != set(METHOD_TYPES):  # pragma: no cover - guarded by a test as well
    raise RuntimeError("rules.REGISTRY and models.METHOD_TYPES diverge")


def evaluate_rule(*, rule: RuleDef, ctx: RuleContext, resolution: ProfileResolution) -> CheckResult:
    """Applicability first (profiles), then the method evaluator."""
    outcome = _applicability_outcome(rule=rule, ctx=ctx, resolution=resolution)
    if outcome is None:
        outcome = REGISTRY[rule.method.type](rule.method, ctx)
    return CheckResult(
        rule_id=rule.id,
        status=outcome.status,
        dimension=rule.dimension,
        severity=rule.severity,
        blocking=rule.blocking,
        message=outcome.message,
        evidence=outcome.evidence,
        error_kind=outcome.error_kind,
    )


def _applicability_outcome(*, rule: RuleDef, ctx: RuleContext, resolution: ProfileResolution) -> Outcome | None:
    metadata_error = ctx.evidence.errors.get("metadata")
    if metadata_error is not None:
        return _from_error(metadata_error, what="repository not collected")
    state = resolution.applicability(rule.profiles)
    if state is Detection.NOT_MATCHED:
        return Outcome(status=Status.NOT_APPLICABLE, message=f"no profile in [{', '.join(rule.profiles)}] matches")
    if state is Detection.UNDETERMINED:
        tree_error = ctx.evidence.errors.get("tree")
        if tree_error is not None:
            return _from_error(tree_error, what="profile detection impossible")
        return Outcome(status=Status.UNKNOWN, message="profile detection incomplete (tree missing or truncated)")
    return None
