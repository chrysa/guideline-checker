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
# Exception names whose catch is blanket rather than targeted.
_BLANKET_EXCEPTIONS = frozenset({"Exception", "BaseException"})
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


# Calls whose presence makes a handler legitimately synchronous. FastAPI runs a
# plain `def` handler in a threadpool, so blocking work belongs there; the same
# body under `async def` would block the event loop for its whole duration.
#
# Deliberately a denylist, and deliberately narrow. Bare HTTP verbs are not in it:
# `requests.get` blocks, `some_dict.get` does not, and a rule that cannot tell them
# apart is the reason this check needed fixing in the first place. Network and
# process work is therefore matched on the *module* it is called through.
_BLOCKING_NAMES = frozenset({"open", "input"})
_BLOCKING_ATTRS = frozenset(
    {
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "mkdir",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "touch",
        "chmod",
        "copy",
        "copytree",
        "rmtree",
        "read",
        "readlines",
        "write",
        "writelines",
        "execute",
        "executemany",
        "commit",
        "fetchone",
        "fetchall",
    }
)
# Modules whose calls block the calling thread whatever the method is named.
_BLOCKING_MODULES = frozenset({"subprocess", "shutil", "requests", "httpx", "urllib", "socket", "time", "os"})


def _blocks(call: ast.Call) -> bool:
    """True when this call does work that must not run on the event loop."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _BLOCKING_NAMES
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _BLOCKING_ATTRS:
        return True
    owner = func.value
    return isinstance(owner, ast.Name) and owner.id in _BLOCKING_MODULES


def _body_blocks(node: ast.FunctionDef) -> bool:
    """True when the handler's *body* performs blocking work.

    Only the body: walking the whole node would sweep in the route decorator, and
    ``@app.get(...)`` reads as an attribute call like any other.
    """
    return any(isinstance(inner, ast.Call) and _blocks(inner) for stmt in node.body for inner in ast.walk(stmt))


def _check_sync_fastapi_route(tree: ast.Module) -> list[tuple[int, str]]:
    """Flag a route handler declared ``def`` while doing nothing that blocks.

    The standard says "do not block the event loop", not "declare handlers async",
    and the two only coincide when the body does no blocking work. A handler that
    writes a file is *correctly* synchronous: FastAPI gives it a threadpool, and
    ``async def`` would stall the loop until the write returned.

    Known limit, not an oversight: blocking work reached through a helper is
    invisible here. ``def persist(...): result = write_derived_ruleset(...)`` still
    fires, because the ``write_text`` lives one call away and a single-file AST pass
    cannot follow it. Findings therefore remain a *warning* that needs a human to
    ask "does this body block?" — see the issue this check was narrowed under.
    """
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue  # AsyncFunctionDef is the compliant case
        if _body_blocks(node):
            continue  # sync on purpose — FastAPI will run it off the loop
        for deco in node.decorator_list:
            if _is_route_decorator(deco):
                findings.append((deco.lineno, f"sync route handler with a non-blocking body: {node.name}"))
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


def _check_silent_exception(tree: ast.Module) -> list[tuple[int, str]]:
    """An exception caught broadly and discarded without a trace.

    ``except Exception: pass`` turns a failure into silence: the program carries
    on with wrong state instead of stopping. A narrow handler that acts is fine,
    so only a bare or blanket catch whose body is nothing but ``pass`` fires.
    """
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body_is_silent = all(isinstance(stmt, ast.Pass) for stmt in node.body)
        catches_everything = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in _BLANKET_EXCEPTIONS
        )
        if body_is_silent and catches_everything:
            findings.append((node.lineno, "exception caught and silently discarded"))
    return findings


def _check_assert_as_validation(tree: ast.Module) -> list[tuple[int, str]]:
    """``assert`` used to guard runtime behaviour.

    Python removes asserts under ``-O``, so a check that protects execution must
    not be one. Test files use ``assert`` legitimately; scoping this check to
    non-test paths is the referential's job, via ``applyTo``.
    """
    return [
        (node.lineno, "assert used as a runtime check; -O removes it")
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]


def _check_unbounded_queue(tree: ast.Module) -> list[tuple[int, str]]:
    """Flag a ``Queue()`` created with no bound (EV-010).

    ``asyncio.Queue`` / ``queue.Queue`` / a bare ``Queue`` default to *unbounded*:
    the first positional argument or the ``maxsize`` keyword is the bound, and its
    absence lets an in-memory queue grow under load until the process dies. A call
    with neither is the finding; ``Queue(100)`` or ``Queue(maxsize=100)`` is fine.

    Known limit: a bound passed through a variable (``Queue(maxsize=cfg.size)``)
    still counts as bounded — the check asks only that a bound is declared, not that
    its value is sane. A single-file AST pass cannot resolve the value.
    """
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name != "Queue":
            continue
        has_maxsize = any(kw.arg == "maxsize" for kw in node.keywords)
        if not node.args and not has_maxsize:
            findings.append((node.lineno, "unbounded queue: Queue() created without a maxsize bound"))
    return findings


_AST_CHECKS: dict[str, AstCheck] = {
    "pydantic-v1": _check_pydantic_v1,
    "sync-fastapi-route": _check_sync_fastapi_route,
    "mutable-default-arg": _check_mutable_default,
    "silent-exception": _check_silent_exception,
    "assert-as-validation": _check_assert_as_validation,
    "unbounded-queue": _check_unbounded_queue,
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
