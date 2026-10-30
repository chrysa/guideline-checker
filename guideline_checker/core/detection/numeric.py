"""Measurement primitives for the ``numeric-threshold`` mechanism (ADR D-0021).

A measurer answers *how much*, never *too much*: it returns the value it read and
the line it read it at, and knows nothing about the bound it will be compared to.
The bound is a host value from ``guidelines/*.yml``; keeping it out of this module
is what stops the engine from carrying a threshold of its own (ADR D-0016).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

from guideline_checker.core.detection import Violation
from guideline_checker.loader import RuleDetector

# What a measurer returns per subject: the line to report at, the measured value,
# and the name of what was measured ("file", or a function's name).
Measurement = tuple[int, int, str]

# The subject name a whole-file metric reports under.
FILE_SUBJECT = "file"

# Nodes that add a decision point. The count is *branches* = decision points + 1,
# the same heuristic the fleet gate's cyclomatic-complexity cap uses.
_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
)
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parse(source: str) -> ast.Module | None:
    """Parse ``source``, or return ``None`` — detection must never crash a scan."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def _functions(source: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function and method in ``source``; empty when it does not parse."""
    tree = _parse(source)
    if tree is None:
        return []
    return [node for node in ast.walk(tree) if isinstance(node, _FUNCTION_NODES)]


def measure_file_lines(source: str) -> list[Measurement]:
    """The file's line count, reported at line 1."""
    return [(1, len(source.splitlines()), FILE_SUBJECT)]


def measure_function_lines(source: str) -> list[Measurement]:
    """Each function's span in lines, reported at its ``def`` line."""
    return [(node.lineno, (node.end_lineno or node.lineno) - node.lineno + 1, node.name) for node in _functions(source)]


def _branch_count(node: ast.AST) -> int:
    """Decision points inside a function, plus one for its entry path."""
    return 1 + sum(1 for inner in ast.walk(node) if isinstance(inner, _BRANCH_NODES))


def measure_branches(source: str) -> list[Measurement]:
    """Each function's branch count, reported at its ``def`` line."""
    return [(node.lineno, _branch_count(node), node.name) for node in _functions(source)]


METRICS: dict[str, Callable[[str], list[Measurement]]] = {
    "file_lines": measure_file_lines,
    "function_lines": measure_function_lines,
    "branches": measure_branches,
}

# Exposed for the YAML loader to validate ``detect.numeric_threshold.metric`` against.
VALID_METRICS: frozenset[str] = frozenset(METRICS)


def _measurement_text(subject: str, value: int, bound: int) -> str:
    """Evidence a human can act on: what was measured, how much, against which bound."""
    what = FILE_SUBJECT if subject == FILE_SUBJECT else f"function {subject!r}"
    return f"{what} measured {value} (max: {bound})"


def _numeric_threshold_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Measure the rule's metric and flag every subject over the host's bound.

    The ``numeric-threshold`` mechanism (ADR D-0021): the engine owns the measuring
    (this module), the metric name and the bound are host values the referential
    supplies. ``max`` is a bound, not a target — reaching it is compliance, only
    crossing it is a violation.
    """
    threshold = detector.numeric_threshold
    if threshold is None:
        return []
    measurer = METRICS[threshold.metric]
    return [
        Violation(
            file=file_path,
            line_number=line,
            line_content=_measurement_text(subject, value, threshold.max_value),
            rule=rule,
            severity="warning",
        )
        for line, value, subject in measurer("\n".join(lines))
        if value > threshold.max_value
    ]


def _function_length_violation(
    file_path: Path,
    func_name: str,
    func_start: int,
    length: int,
    limit: int,
) -> Violation:
    """Build a Violation for a function that exceeds the line-count limit."""
    return Violation(
        file=file_path,
        line_number=func_start,
        line_content=f"Function '{func_name}' has {length} lines (limit: {limit})",
        rule=f"max function length: {limit}",
        severity="warning",
    )


def _check_function_lengths(file_path: Path, lines: list[str], limit: int) -> list[Violation]:
    """Flag Python functions that exceed a line-count limit."""
    violations: list[Violation] = []
    func_start: int | None = None
    func_name = ""

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not re.match(r"(async\s+)?def\s+\w+", stripped):
            continue
        if func_start is not None:
            length = lineno - func_start
            if length > limit:
                violations.append(_function_length_violation(file_path, func_name, func_start, length, limit))
        func_start = lineno
        m = re.search(r"def\s+(\w+)", stripped)
        func_name = m.group(1) if m else "<anonymous>"

    # Check last function
    if func_start is not None:
        length = len(lines) - func_start + 1
        if length > limit:
            violations.append(_function_length_violation(file_path, func_name, func_start, length, limit))

    return violations


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
