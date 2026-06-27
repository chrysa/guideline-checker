"""Drive the distribution audit across a fleet manifest, origin-side.

Wraps each repo's distribution violations in a synthetic ``RuleResult`` so the
existing synthesis reporter consumes origin findings with no reporter changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.checker import RuleResult, Violation
from guideline_checker.distribution import CHECK_IDS, Expectations, audit
from guideline_checker.gh_client import GhClient
from guideline_checker.loader import InstructionFile, SourceType
from guideline_checker.manifest import RepoTarget
from guideline_checker.scanner_source import OriginScanner


@dataclass
class DistRepoResult:
    name: str
    results: list[RuleResult] = field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    fetch_failed: bool = False


def _synthetic_instruction() -> InstructionFile:
    return InstructionFile(
        path=Path("<distribution>"),
        apply_to="",
        description="distribution compliance",
        content="",
        source_type=SourceType.GUIDELINES_YAML,
        rules=list(CHECK_IDS),
    )


def _wrap(violations: list[Violation]) -> DistRepoResult:
    result = RuleResult(instruction=_synthetic_instruction(), violations=violations, files_checked=len(CHECK_IDS))
    errors = sum(1 for v in violations if v.severity == "error")
    warnings = sum(1 for v in violations if v.severity == "warning")
    return DistRepoResult(name="", results=[result], errors=errors, warnings=warnings)


def run_origin_audit(targets: list[RepoTarget], expected: Expectations, client: GhClient) -> list[DistRepoResult]:
    out: list[DistRepoResult] = []
    for target in targets:
        if not client.repo_exists(target.owner, target.name):
            failure = Violation(
                file=Path("<origin>"),
                line_number=1,
                line_content=f"cannot reach origin for {target.owner}/{target.name}",
                rule="origin-fetch-failed",
                severity="error",
            )
            wrapped = _wrap([failure])
            wrapped.name, wrapped.fetch_failed = target.name, True
            out.append(wrapped)
            continue
        scanner = OriginScanner(target.owner, target.name, client)
        wrapped = _wrap(audit(scanner, target, expected))
        wrapped.name = target.name
        out.append(wrapped)
    return out
