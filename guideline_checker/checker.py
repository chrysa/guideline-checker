"""Core checker: match files against instruction rules."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

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


class PatternCheck(NamedTuple):
    """A single pattern check derived from a rule sentence."""

    pattern: str
    severity: str
    match_in_comments: bool = False


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


def run_checks(root: Path, instructions_dir: Path) -> list[RuleResult]:
    """Check all files in root against all instruction files in instructions_dir."""
    instructions = load_instructions(instructions_dir)
    all_files = _collect_files(root)
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


def _collect_files(root: Path) -> list[Path]:
    """Recursively collect all files, ignoring known irrelevant directories."""
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in IGNORE_DIRS or part.endswith(".egg-info") for part in path.parts)
    ]


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
        for check in checks:
            if _line_matches(line, check.pattern, match_in_comments=check.match_in_comments):
                violations.append(
                    Violation(
                        file=file_path,
                        line_number=lineno,
                        line_content=line.strip()[:120],
                        rule=rule,
                        severity=check.severity,
                    ),
                )
                break  # one violation per line per rule

    return violations


def _build_checks(rule_lower: str) -> list[PatternCheck]:
    """Build anti-pattern checks from rule text. Returns list of PatternCheck."""
    checks: list[PatternCheck] = []
    checks.extend(_debug_output_checks(rule_lower))
    checks.extend(_exception_checks(rule_lower))
    checks.extend(_dangerous_builtin_checks(rule_lower))
    checks.extend(_import_checks(rule_lower))
    checks.extend(_annotation_checks(rule_lower))
    checks.extend(_hygiene_checks(rule_lower))
    checks.extend(_credential_checks(rule_lower))
    return checks


def _debug_output_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if "no print" in rule_lower or "print()" in rule_lower:
        checks.append(PatternCheck("print(", "warning"))
    if "no pprint" in rule_lower or "pprint()" in rule_lower:
        checks.append(PatternCheck("pprint(", "warning"))
    if "no console.log" in rule_lower:
        checks.append(PatternCheck("console.log(", "warning"))
    if "no console.debug" in rule_lower:
        checks.append(PatternCheck("console.debug(", "warning"))
    if "no debugger" in rule_lower:
        checks.append(PatternCheck("debugger", "warning"))
    return checks


def _exception_checks(rule_lower: str) -> list[PatternCheck]:
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        return [PatternCheck("except:", "error")]
    return []


def _dangerous_builtin_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if "no eval" in rule_lower:
        checks.append(PatternCheck("eval(", "error"))
    if "no exec" in rule_lower:
        checks.append(PatternCheck("exec(", "error"))
    return checks


def _import_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no import *", "no wildcard import", "no star import")):
        checks.append(PatternCheck("import *", "error"))
    if any(phrase in rule_lower for phrase in ("no relative import", "absolute import")):
        checks.append(PatternCheck("from . import", "warning"))
        checks.append(PatternCheck("from .. import", "warning"))
    return checks


def _annotation_checks(rule_lower: str) -> list[PatternCheck]:
    if "from __future__ import annotations" in rule_lower:
        return [PatternCheck("__future__", "info")]
    return []


def _hygiene_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if "no todo" in rule_lower:
        checks.append(PatternCheck("TODO", "warning", match_in_comments=True))
    if "no fixme" in rule_lower:
        checks.append(PatternCheck("FIXME", "warning", match_in_comments=True))
    if "no hack" in rule_lower:
        checks.append(PatternCheck("HACK", "warning", match_in_comments=True))
    if "no assert" in rule_lower and "test" not in rule_lower:
        checks.append(PatternCheck("assert ", "warning"))
    return checks


def _credential_checks(rule_lower: str) -> list[PatternCheck]:
    _secret_keywords = ("secret", "password", "credential", "key", "token")
    if "no hardcoded" not in rule_lower or not any(kw in rule_lower for kw in _secret_keywords):
        return []
    return [
        PatternCheck(kw, "error") for kw in ("password =", "password=", "secret =", "secret=", "api_key =", "api_key=")
    ]


def _line_matches(line: str, pattern: str, *, match_in_comments: bool = False) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments by default)."""
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern.lower() in stripped.lower()
