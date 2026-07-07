"""Baseline support for incremental adoption (L2.2).

A baseline records the fingerprints of the violations a project currently accepts,
so subsequent runs fail only on *new* violations. This lets a repo adopt the checker
with ``--fail-on error`` from day one without drowning in its existing backlog.

Fingerprints are content-based — a hash of ``rule id + repo-relative path + the
stripped matched line`` — rather than line-number based. An edit that shifts a
baselined violation up or down the file therefore does not resurface it. The
trade-off is that two identical matches (same rule, file, and line text) collapse
to a single fingerprint; baselining one baselines both. This is acceptable for an
adoption baseline and keeps the format drift-tolerant.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from guideline_checker.checker import RuleResult, Violation

BASELINE_VERSION = 1


@dataclass
class BaselineFilter:
    """Outcome of applying a baseline to a run's results.

    ``results`` holds the same :class:`RuleResult` list with only the *new*
    (non-baselined) violations retained, so it can flow into the reporters and the
    exit-code logic unchanged.
    """

    results: list[RuleResult]
    new_count: int = 0
    baselined_count: int = 0


def apply_baseline(results: list[RuleResult], baseline: set[str], root: Path) -> BaselineFilter:
    """Partition ``results`` into new vs baselined violations."""
    filtered: list[RuleResult] = []
    new_count = 0
    baselined_count = 0
    for result in results:
        kept: list[Violation] = []
        for violation in result.violations:
            if fingerprint(violation, root) in baseline:
                baselined_count += 1
            else:
                kept.append(violation)
                new_count += 1
        filtered.append(
            RuleResult(
                instruction=result.instruction,
                violations=kept,
                files_checked=result.files_checked,
            )
        )
    return BaselineFilter(results=filtered, new_count=new_count, baselined_count=baselined_count)


def collect_fingerprints(results: list[RuleResult], root: Path) -> set[str]:
    """Return the fingerprint set for every violation across ``results``."""
    return {fingerprint(v, root) for result in results for v in result.violations}


def fingerprint(violation: Violation, root: Path) -> str:
    """Stable, line-number-independent fingerprint of a single violation."""
    rel = _relative_path(violation.file, root)
    raw = f"{violation.rule}\0{rel}\0{violation.line_content.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_baseline(path: Path) -> set[str]:
    """Load the fingerprint set from a baseline file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("fingerprints", []))


def write_baseline(results: list[RuleResult], root: Path, path: Path) -> int:
    """Write a deterministic, diff-stable baseline file; return the fingerprint count."""
    fingerprints = sorted(collect_fingerprints(results, root))
    payload = {"version": BASELINE_VERSION, "fingerprints": fingerprints}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(fingerprints)


def _relative_path(file: Path, root: Path) -> str:
    """Repo-relative POSIX path, falling back to the raw path when outside root."""
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.as_posix()
