"""Remediation producers + PR planner for distribution drift.

Opt-in. Opens ONE PR per repo; never merges. Idempotent: an existing fix branch/PR
short-circuits. ``precommit-pin`` and ``claude-import`` need the current file content
(append/inject), so they are applied only when the file already exists; a wholly
missing pre-commit/CLAUDE file is reported but left for a human (no safe full-file template).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from guideline_checker.core.detection import RuleResult
from guideline_checker.fleet.distribution import (
    LICENSE_PATH,
    STANDARDS_PATH,
    Expectations,
)
from guideline_checker.fleet.gh_client import GhClient

_FIX_BRANCH = "chore/distribution-fixes"

# Only checks with a safe whole-file remediation are auto-fixable here.
FIX_CONTENT: dict[str, Callable[[Expectations], str]] = {
    "license-present": lambda exp: exp.license_text,
    "standards-file": lambda exp: exp.canonical_standards,
}
ARTIFACT_PATH: dict[str, str] = {
    "license-present": LICENSE_PATH,
    "standards-file": STANDARDS_PATH,
}


@dataclass
class FixPlan:
    repo: str
    paths: list[str]
    dry_run: bool


def plan_fixes(repo_result: RuleResult, _expected: Expectations) -> FixPlan:
    paths = [ARTIFACT_PATH[v.rule] for v in repo_result.violations if v.rule in FIX_CONTENT]
    return FixPlan(repo="", paths=paths, dry_run=False)


def apply_fix(
    owner: str,
    repo: str,
    repo_result: RuleResult,
    expected: Expectations,
    client: GhClient,
    dry_run: bool,
) -> str | None:
    fixable = [v for v in repo_result.violations if v.rule in FIX_CONTENT]
    if not fixable:
        return None
    if dry_run:
        return "DRY-RUN"
    existing = client.find_pr(owner, repo, _FIX_BRANCH)
    if existing is not None:
        return existing
    base = client.default_branch(owner, repo)
    sha = client.branch_sha(owner, repo, base)
    if sha is None or not client.create_branch(owner, repo, _FIX_BRANCH, sha):
        return None
    for v in fixable:
        content = FIX_CONTENT[v.rule](expected)
        client.put_file(
            owner, repo, ARTIFACT_PATH[v.rule], content, f"chore: fix {v.rule} distribution drift", _FIX_BRANCH
        )
    body = "Automated distribution-drift remediation by guideline-checker.\n\nRefs: standards distribution."
    return client.open_pr(owner, repo, _FIX_BRANCH, base, "chore: fix standards distribution drift", body)
