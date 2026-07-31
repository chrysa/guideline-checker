"""Measurement primitives for the ``numeric-threshold`` mechanism (ADR D-0021).

A measurer answers *how much*, never *too much*: it returns the value it read and
the line it read it at, and knows nothing about the bound it will be compared to.
The bound is a host value from ``guidelines/*.yml``; keeping it out of this module
is what stops the engine from carrying a threshold of its own (ADR D-0016).
"""

from __future__ import annotations

import ast
from collections.abc import Callable

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
