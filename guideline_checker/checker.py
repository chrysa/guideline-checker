"""Core checker: match files against instruction rules."""

from __future__ import annotations

import fnmatch
import re
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

# Inline suppression marker — add this comment on any line to skip all rule checks
DISABLE_COMMENT = "guideline: disable"


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

    # Length-based rules are handled separately (need the full file)
    length_violations = _check_length_rules(file_path, lines, rule_lower)
    if length_violations:
        return length_violations

    # Detect common anti-patterns based on rule text
    checks = _build_checks(rule_lower)

    for lineno, line in enumerate(lines, start=1):
        # Inline suppression: skip lines marked with the disable comment
        if DISABLE_COMMENT in line:
            continue
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
    checks.extend(_typescript_checks(rule_lower))
    checks.extend(_python_strict_checks(rule_lower))
    checks.extend(_security_checks(rule_lower))
    return checks


def _check_length_rules(file_path: Path, lines: list[str], rule_lower: str) -> list[Violation]:
    """Check file/function length rules that operate on the whole file."""
    violations: list[Violation] = []

    # Max file length: "max file length: N" or "max N lines per file"
    match = re.search(r"max(?:imum)?\s+file\s+length[:\s]+(\d+)", rule_lower) or re.search(
        r"max\s+(\d+)\s+lines?\s+per\s+file", rule_lower
    )
    if match:
        limit = int(match.group(1))
        if len(lines) > limit:
            violations.append(
                Violation(
                    file=file_path,
                    line_number=1,
                    line_content=f"File has {len(lines)} lines (limit: {limit})",
                    rule=f"max file length: {limit}",
                    severity="warning",
                )
            )

    return violations


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


def _typescript_checks(rule_lower: str) -> list[PatternCheck]:
    """TypeScript / React anti-pattern checks."""
    checks: list[PatternCheck] = []
    if "no any" in rule_lower or "no `any`" in rule_lower or "avoid any" in rule_lower:
        checks.append(PatternCheck(": any", "error"))
        checks.append(PatternCheck("as any", "error"))
    if "no ts-ignore" in rule_lower or "no @ts-ignore" in rule_lower:
        checks.append(PatternCheck("@ts-ignore", "error", match_in_comments=True))
    if "no ts-nocheck" in rule_lower or "no @ts-nocheck" in rule_lower:
        checks.append(PatternCheck("@ts-nocheck", "error", match_in_comments=True))
    if "no console.log" in rule_lower:
        checks.append(PatternCheck("console.log(", "warning"))
    if "no console.debug" in rule_lower:
        checks.append(PatternCheck("console.debug(", "warning"))
    if "no console.warn" in rule_lower:
        checks.append(PatternCheck("console.warn(", "warning"))
    if "no inline style" in rule_lower or "no inline styles" in rule_lower:
        checks.append(PatternCheck("style={{", "warning"))
    return checks


def _python_strict_checks(rule_lower: str) -> list[PatternCheck]:
    """Strict Python quality checks."""
    checks: list[PatternCheck] = []
    if "no global" in rule_lower and "global statement" in rule_lower:
        checks.append(PatternCheck("global ", "error"))
    if "no pass in except" in rule_lower or "no silent exception" in rule_lower:
        checks.append(PatternCheck("except:", "error"))
    if "no mutable default" in rule_lower:
        checks.append(PatternCheck("=[]", "warning"))
        checks.append(PatternCheck("={}", "warning"))
    if "no type: ignore" in rule_lower or "no type:ignore" in rule_lower:
        checks.append(PatternCheck("type: ignore", "error", match_in_comments=True))
        checks.append(PatternCheck("type:ignore", "error", match_in_comments=True))
    return checks


def _security_checks(rule_lower: str) -> list[PatternCheck]:
    """Security-oriented checks (OWASP-aligned)."""
    checks: list[PatternCheck] = []
    if "no hardcoded url" in rule_lower or "no hardcoded urls" in rule_lower:
        checks.append(PatternCheck("http://", "warning"))
        checks.append(PatternCheck("https://", "info"))
    if "no hardcoded ip" in rule_lower:
        checks.append(PatternCheck("127.0.0.1", "warning"))
        checks.append(PatternCheck("0.0.0.0", "warning"))  # noqa: S104
    if "no shell=true" in rule_lower or "no shell injection" in rule_lower:
        checks.append(PatternCheck("shell=True", "error"))
    if "no pickle" in rule_lower:
        checks.append(PatternCheck("import pickle", "error"))
        checks.append(PatternCheck("pickle.load", "error"))
    return checks


def _line_matches(line: str, pattern: str, *, match_in_comments: bool = False) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments by default)."""
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern.lower() in stripped.lower()
