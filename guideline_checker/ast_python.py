"""Precise Python detectors backed by the standard-library :mod:`ast`.

Substring/regex detection (the ``detect.forbid`` / ``detect.file_regex`` paths)
is blunt: ``from pydantic import validator`` matches even inside a string literal
or a comment, and a sync-vs-async route check written as a regex is brittle about
spacing and decorator arguments. These detectors parse the file instead, so they
fire only on the real construct.

Checks are *named* and selected from a YAML rule via ``detect.ast: [<name>]`` —
keeping the "rules as data" contract: the referential declares which check it
wants, no checker edit required to wire a shipped rule.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Sequence

# A check maps a parsed module to ``(lineno, snippet)`` findings.
AstCheck = Callable[[ast.Module], list[tuple[int, str]]]

# Pydantic names whose import signals v1 API (removed or relocated in v2).
_PYDANTIC_V1_NAMES = frozenset({"validator", "root_validator", "BaseSettings"})
# Decorators that are v1-only validator helpers.
_PYDANTIC_V1_DECORATORS = frozenset({"validator", "root_validator"})
# HTTP method attributes that mark a FastAPI/Starlette/APIRouter route decorator.
_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
# Identifiers a route decorator is typically called on (``@app.get`` / ``@router.post``).
_ROUTE_OWNERS = frozenset({"app", "router"})


def _decorator_name(node: ast.expr) -> str | None:
    """Return the simple name a decorator references (``@validator`` → ``validator``)."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _check_pydantic_v1(tree: ast.Module) -> list[tuple[int, str]]:
    """Flag Pydantic v1 imports and validator decorators."""
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pydantic" and any(a.name in _PYDANTIC_V1_NAMES for a in node.names):
                hit = ", ".join(a.name for a in node.names if a.name in _PYDANTIC_V1_NAMES)
                findings.append((node.lineno, f"pydantic v1 import: {hit}"))
            elif module == "pydantic.class_validators" or module.startswith("pydantic.v1"):
                findings.append((node.lineno, f"pydantic v1 import: from {module}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            findings.extend(
                (deco.lineno, f"pydantic v1 decorator: @{name}")
                for deco in node.decorator_list
                if (name := _decorator_name(deco)) in _PYDANTIC_V1_DECORATORS
            )
    return findings


def _is_route_decorator(node: ast.expr) -> bool:
    """True for ``@app.get(...)`` / ``@router.post(...)``-style route decorators."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    attr = node.func
    if attr.attr not in _ROUTE_METHODS:
        return False
    owner = attr.value
    if isinstance(owner, ast.Name):
        return owner.id in _ROUTE_OWNERS
    if isinstance(owner, ast.Attribute):
        return owner.attr in _ROUTE_OWNERS
    return False


def _check_sync_fastapi_route(tree: ast.Module) -> list[tuple[int, str]]:
    """Flag a route decorator applied to a plain (non-async) handler."""
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue  # AsyncFunctionDef is the compliant case
        for deco in node.decorator_list:
            if _is_route_decorator(deco):
                findings.append((deco.lineno, f"sync route handler: {node.name}"))
                break
    return findings


# Builtins whose call produces a fresh mutable container (``def f(x=dict())``).
_MUTABLE_FACTORIES = frozenset({"list", "dict", "set"})


def _is_mutable_default(node: ast.expr) -> bool:
    """True for a default that is a shared mutable: ``[]`` / ``{}`` / ``set()`` etc."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MUTABLE_FACTORIES


def _check_mutable_default(tree: ast.Module) -> list[tuple[int, str]]:
    """Flag function parameters whose default value is a shared mutable container."""
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = [*node.args.defaults, *node.args.kw_defaults]
        findings.extend(
            (d.lineno, f"mutable default argument in {node.name}()")
            for d in defaults
            if d is not None and _is_mutable_default(d)
        )
    return findings


_AST_CHECKS: dict[str, AstCheck] = {
    "pydantic-v1": _check_pydantic_v1,
    "sync-fastapi-route": _check_sync_fastapi_route,
    "mutable-default-arg": _check_mutable_default,
}

# Exposed for the YAML loader to validate ``detect.ast`` names against.
VALID_AST_CHECKS: frozenset[str] = frozenset(_AST_CHECKS)


def run_ast_checks(names: Sequence[str], source: str) -> list[tuple[int, str]]:
    """Parse ``source`` once and run the named checks, deduped and line-sorted.

    Unknown names are ignored (the loader validates them up front). A file that
    does not parse (syntax error / non-Python) yields no findings rather than
    raising — detection must never crash the scan.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    seen: set[tuple[int, str]] = set()
    out: list[tuple[int, str]] = []
    for name in names:
        check = _AST_CHECKS.get(name)
        if check is None:
            continue
        for finding in check(tree):
            if finding not in seen:
                seen.add(finding)
                out.append(finding)
    out.sort(key=lambda f: f[0])
    return out


def unknown_checks(names: Iterable[str]) -> list[str]:
    """Return the names that are not registered checks (for validation messages)."""
    return [n for n in names if n not in _AST_CHECKS]
