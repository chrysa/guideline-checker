"""Core checker: match files against instruction rules."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.loader import InstructionFile, load_instructions

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".eggs",
    "*.egg-info",
}


@dataclass
class Violation:
    file: Path
    line_number: int
    line_content: str
    rule: str
    severity: str = "warning"


@dataclass
class RuleResult:
    instruction: InstructionFile
    violations: list[Violation] = field(default_factory=list)
    files_checked: int = 0


def run_checks(
    root: Path,
    instructions_dir: Path,
    excludes: list[str] | None = None,
) -> list[RuleResult]:
    """Check all files in root against all instruction files in instructions_dir.

    :param root: Project root directory to scan.
    :param instructions_dir: Directory containing .instructions.md files.
    :param excludes: Optional glob patterns (relative to root) to skip.
    """
    instructions = load_instructions(instructions_dir)
    all_files = _collect_files(root, excludes or [])
    results: list[RuleResult] = []

    for instruction in instructions:
        result = RuleResult(instruction=instruction)
        matched_files = [f for f in all_files if _matches_pattern(f, root, instruction.apply_to)]
        result.files_checked = len(matched_files)
        for file_path in matched_files:
            violations = _check_file(file_path, instruction)
            result.violations.extend(violations)
        results.append(result)

    return results


def _collect_files(root: Path, excludes: list[str]) -> list[Path]:
    """Recursively collect all files, ignoring known irrelevant directories.

    Files matching any of the ``excludes`` glob patterns (relative to root)
    are skipped.
    """
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in IGNORE_DIRS or part.endswith(".egg-info") for part in path.parts)
        and not _is_excluded(path, root, excludes)
    ]


def _is_excluded(file_path: Path, root: Path, excludes: list[str]) -> bool:
    """Return True if file_path matches any exclude pattern."""
    if not excludes:
        return False
    return any(_matches_pattern(file_path, root, pat) for pat in excludes)


def _matches_pattern(file_path: Path, root: Path, pattern: str) -> bool:
    """Check if a file path matches a glob pattern (relative to root).

    Supports ``**`` recursive wildcards via :meth:`pathlib.PurePath.match`
    with a fallback for root-level files (Python 3.12 compat).
    Comma-separated patterns are treated as alternatives (match any).
    """
    try:
        relative = file_path.relative_to(root)
    except ValueError:
        return False

    patterns = [p.strip() for p in pattern.split(",") if p.strip()]
    for pat in patterns:
        if relative.match(pat):
            return True
        # Python 3.12: PurePath.match("**/*.ext") won't match root-level
        # files. Strip the leading **/ and try matching the filename.
        if pat.startswith("**/") and fnmatch.fnmatch(file_path.name, pat[3:]):
            return True
    return False


def _check_file(file_path: Path, instruction: InstructionFile) -> list[Violation]:
    """Check a single file against an instruction's rules."""
    violations: list[Violation] = []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return violations

    for rule in instruction.rules:
        rule_violations = _evaluate_rule(file_path, lines, rule)
        violations.extend(rule_violations)

    return violations


def _evaluate_rule(file_path: Path, lines: list[str], rule: str) -> list[Violation]:
    """Evaluate a natural-language rule against file lines (basic pattern matching)."""
    violations: list[Violation] = []
    rule_lower = rule.lower()

    # Detect common anti-patterns based on rule text
    checks = _build_checks(rule_lower)

    for lineno, line in enumerate(lines, start=1):
        for pattern, severity in checks:
            if _line_matches(line, pattern):
                violations.append(
                    Violation(
                        file=file_path,
                        line_number=lineno,
                        line_content=line.strip()[:120],
                        rule=rule,
                        severity=severity,
                    ),
                )
                break  # one violation per line per rule

    return violations


def _build_checks(rule_lower: str) -> list[tuple[str, str]]:
    """Build anti-pattern checks from rule text. Returns list of (pattern, severity)."""
    checks: list[tuple[str, str]] = []

    # Python: print / pprint
    if "no print" in rule_lower or "print()" in rule_lower:
        checks.append(("print(", "warning"))
    if "no pprint" in rule_lower or "pprint()" in rule_lower:
        checks.append(("pprint(", "warning"))

    # JavaScript/TypeScript console
    if "no console.log" in rule_lower:
        checks.append(("console.log(", "warning"))
    if "no console.debug" in rule_lower:
        checks.append(("console.debug(", "warning"))
    if "no console.warn" in rule_lower:
        checks.append(("console.warn(", "warning"))
    if "no console.error" in rule_lower:
        checks.append(("console.error(", "warning"))

    # Debugger statements
    if "no debugger" in rule_lower:
        checks.append(("debugger", "error"))
    if "no breakpoint" in rule_lower or "breakpoint()" in rule_lower:
        checks.append(("breakpoint(", "error"))

    # TODO / FIXME comments
    if "no todo" in rule_lower:
        checks.append(("TODO", "info"))
    if "no fixme" in rule_lower:
        checks.append(("FIXME", "warning"))
    if "no xxx" in rule_lower:
        checks.append(("XXX", "warning"))
    if "no hack" in rule_lower:
        checks.append(("HACK", "warning"))

    # Python-specific anti-patterns
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        checks.append(("except:", "error"))
    if "from __future__ import annotations" in rule_lower:
        checks.append(("__future__", "info"))

    # Secrets / credentials (basic heuristics, not security-grade)
    if "no hardcoded password" in rule_lower or "no password" in rule_lower:
        checks.append(("password =", "error"))
        checks.append(("password=", "error"))
    if "no hardcoded api key" in rule_lower or "no api key" in rule_lower:
        checks.append(("api_key =", "error"))
        checks.append(("apikey =", "error"))

    return checks


def _line_matches(line: str, pattern: str) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments)."""
    stripped = line.strip()
    if stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern in stripped
