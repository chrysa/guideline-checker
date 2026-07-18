"""Remediation producers + PR planner for distribution drift.

Opt-in. Opens ONE PR per repo; never merges. Idempotent: an existing fix branch/PR
short-circuits. ``license-present`` has a safe whole-file template; ``standards-block``
injects/refreshes the managed ``chrysa:standards`` block inside the current CLAUDE.md
(read-modify-write). ``precommit-pin`` needs bespoke merge logic and is left for a human.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from guideline_checker.checker import RuleResult
from guideline_checker.distribution import (
    CLAUDE_PATH,
    LICENSE_PATH,
    Expectations,
    inject_standards_block,
)
from guideline_checker.gh_client import GhClient

_FIX_BRANCH = "chore/distribution-fixes"

# Whole-file template fixers (content depends only on the expectations).
FIX_CONTENT: dict[str, Callable[[Expectations], str]] = {
    "license-present": lambda exp: exp.license_text,
}
# Every auto-fixable check → its target artifact. ``standards-block`` is spliced into
# CLAUDE.md rather than templated, so it lives here but not in ``FIX_CONTENT``.
ARTIFACT_PATH: dict[str, str] = {
    "license-present": LICENSE_PATH,
    "standards-block": CLAUDE_PATH,
}


@dataclass
class FixPlan:
    repo: str
    paths: list[str]
    dry_run: bool


def plan_fixes(repo_result: RuleResult, _expected: Expectations) -> FixPlan:
    paths = [ARTIFACT_PATH[v.rule] for v in repo_result.violations if v.rule in ARTIFACT_PATH]
    return FixPlan(repo="", paths=paths, dry_run=False)


def _artifact_content(owner: str, repo: str, rule: str, expected: Expectations, client: GhClient, base: str) -> str:
    if rule in FIX_CONTENT:
        return FIX_CONTENT[rule](expected)
    current = client.read_file(owner, repo, ARTIFACT_PATH[rule], base)
    return inject_standards_block(current, expected)


def apply_fix(
    owner: str,
    repo: str,
    repo_result: RuleResult,
    expected: Expectations,
    client: GhClient,
    dry_run: bool,
) -> str | None:
    fixable = [v.rule for v in repo_result.violations if v.rule in ARTIFACT_PATH]
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
    for rule in fixable:
        content = _artifact_content(owner, repo, rule, expected, client, base)
        client.put_file(owner, repo, ARTIFACT_PATH[rule], content, f"chore: fix {rule} distribution drift", _FIX_BRANCH)
    body = "Automated distribution-drift remediation by guideline-checker.\n\nRefs: standards distribution."
    return client.open_pr(owner, repo, _FIX_BRANCH, base, "chore: fix standards distribution drift", body)
