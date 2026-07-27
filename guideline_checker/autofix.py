"""Local, deterministic autofix for violations on rules carrying a ``fix:`` block.

Distinct from :mod:`guideline_checker.fixers`, which opens remote distribution-fix PRs.
This module rewrites the *local working tree* for the exact lines that a fixable rule
flagged. Detection stays the source of truth — a line is only touched where a violation
fired. All operations are mechanical and idempotent (ADR D-0017).
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.checker import RuleResult
from guideline_checker.loader import RuleFix


@dataclass
class FixReport:
    """Outcome of an autofix pass."""

    fixed_count: int = 0
    changed_files: list[Path] = field(default_factory=list)
    diff: str = ""  # populated only in dry-run mode


def apply_local_fixes(
    results: list[RuleResult],
    root: Path,
    rule_fixes: dict[str, RuleFix],
    *,
    dry_run: bool,
) -> FixReport:
    """Apply every rule's ``fix:`` to the lines it flagged; return what changed."""
    edits_by_file = _collect_edits(results, root, rule_fixes)
    report = FixReport()
    diffs: list[str] = []
    for path, edits in edits_by_file.items():
        original = path.read_text(encoding="utf-8")
        rewritten, applied = _rewrite(original, edits)
        if applied == 0 or rewritten == original:
            continue
        report.fixed_count += applied
        report.changed_files.append(path)
        if dry_run:
            diffs.append(_unified_diff(original, rewritten, path, root))
        else:
            path.write_text(rewritten, encoding="utf-8")
    report.diff = "".join(diffs)
    return report


def _collect_edits(
    results: list[RuleResult], root: Path, rule_fixes: dict[str, RuleFix]
) -> dict[Path, list[tuple[int, RuleFix]]]:
    """Group (line, fix) edits by absolute file path for every fixable violation."""
    edits: dict[Path, list[tuple[int, RuleFix]]] = defaultdict(list)
    for result in results:
        for violation in result.violations:
            fix = rule_fixes.get(violation.rule)
            if fix is not None:
                edits[_resolve(violation.file, root)].append((violation.line_number, fix))
    return edits


def _resolve(file: Path, root: Path) -> Path:
    return file if file.is_absolute() else (root / file)


def _rewrite(text: str, edits: list[tuple[int, RuleFix]]) -> tuple[str, int]:
    """Apply per-line edits; return the new text and the count that changed a line."""
    fixes_by_line: dict[int, list[RuleFix]] = defaultdict(list)
    for line_number, fix in edits:
        fixes_by_line[line_number].append(fix)

    out: list[str] = []
    applied = 0
    for index, line in enumerate(text.splitlines(keepends=True), start=1):
        fixes = fixes_by_line.get(index)
        if not fixes:
            out.append(line)
            continue
        if any(f.op == "remove_line" for f in fixes):
            applied += 1  # the line is dropped entirely
            continue
        new_line = _apply_line_fixes(line, fixes)
        applied += new_line != line
        out.append(new_line)
    return "".join(out), applied


def _apply_line_fixes(line: str, fixes: list[RuleFix]) -> str:
    for fix in fixes:
        if fix.op == "replace":
            line = line.replace(fix.search, fix.replacement)
        elif fix.op == "regex_replace":
            line = re.sub(fix.search, fix.replacement, line)
    return line


def _unified_diff(original: str, rewritten: str, path: Path, root: Path) -> str:
    label = _relative(path, root)
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            rewritten.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
