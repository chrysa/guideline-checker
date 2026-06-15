"""Precise JavaScript / TypeScript detectors backed by tree-sitter.

The text detectors (``detect.forbid`` / ``detect.forbid_regex`` / ``detect.file_regex``)
are blunt for JS/TS: ``: any`` matches inside a string, a conditional ``useState`` call
is invisible to a line regex, and a component defined inside another component's render
is a structural fact no substring can see. These detectors parse the file with
tree-sitter (TS/TSX/JS/JSX grammars) so they fire only on the real construct.

Checks are *named* and selected from a YAML rule via ``detect.ast: [<name>]`` — the same
"rules as data" contract as :mod:`guideline_checker.ast_python`. The two registries are
disjoint; :func:`run_js_ast_checks` runs for JS/TS file suffixes, the Python engine for
``.py``. See ADR D-0006.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence

import tree_sitter_javascript as _ts_js
import tree_sitter_typescript as _ts_ts
from tree_sitter import Language, Node, Parser

# A check maps a parsed tree root to ``(lineno, snippet)`` findings.
JsAstCheck = Callable[[Node], list[tuple[int, str]]]

# File suffix → tree-sitter grammar. JSX is handled by the JavaScript grammar,
# TSX by the dedicated TypeScript-TSX grammar.
_SUFFIX_GRAMMAR: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
}

JS_SUFFIXES: frozenset[str] = frozenset(_SUFFIX_GRAMMAR)

# Tree-sitter node types that introduce a function scope (a hook called below one of
# these, relative to its component, is still top-level; a nested *component* def is the
# inline-component anti-pattern).
_FUNCTION_TYPES = frozenset(
    {"arrow_function", "function_declaration", "function_expression", "method_definition"},
)
# Control-flow constructs that make an enclosed hook call conditional.
_CONDITIONAL_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "ternary_expression",
        "catch_clause",
    },
)
# JSX expression node types (a function returning one of these is a component).
_JSX_TYPES = frozenset({"jsx_element", "jsx_self_closing_element", "jsx_fragment"})

_HOOK_NAME = re.compile(r"^use([A-Z]\w*)?$")

_languages: dict[str, Language] = {}


def _language(kind: str) -> Language:
    """Build (and cache) the tree-sitter ``Language`` for a grammar kind."""
    if kind not in _languages:
        if kind == "typescript":
            _languages[kind] = Language(_ts_ts.language_typescript())
        elif kind == "tsx":
            _languages[kind] = Language(_ts_ts.language_tsx())
        else:
            _languages[kind] = Language(_ts_js.language())
    return _languages[kind]


def _parse(source: str, suffix: str) -> Node | None:
    """Parse ``source`` with the grammar for ``suffix``; ``None`` for an unknown suffix."""
    kind = _SUFFIX_GRAMMAR.get(suffix)
    if kind is None:
        return None
    parser = Parser(_language(kind))
    return parser.parse(bytes(source, "utf-8")).root_node


def _text(node: Node | None) -> str:
    """Return a node's source text (tree-sitter exposes it as bytes)."""
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", "replace")


def _line(node: Node) -> int:
    """1-based start line of a node (tree-sitter rows are 0-based)."""
    return node.start_point[0] + 1


def _walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal over a node and all its descendants."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _enclosing_function(node: Node) -> Node | None:
    """Nearest ancestor that introduces a function scope, or ``None``."""
    parent = node.parent
    while parent is not None:
        if parent.type in _FUNCTION_TYPES:
            return parent
        parent = parent.parent
    return None


def _first_identifier(node: Node) -> str | None:
    """First ``identifier`` name within a parameter node (handles JS bare params and the
    TS ``required_parameter`` wrapper)."""
    if node.type == "identifier":
        return _text(node)
    for child in node.named_children:
        name = _first_identifier(child)
        if name is not None:
            return name
    return None


def _param_names(func: Node) -> list[str]:
    """Ordered parameter names of a function-like node."""
    params = func.child_by_field_name("parameters")
    if params is None:
        return []
    names: list[str] = []
    for child in params.named_children:
        name = _first_identifier(child)
        if name is not None:
            names.append(name)
    return names


# ─── ts-any-type ──────────────────────────────────────────────────────────────


def _check_any_type(root: Node) -> list[tuple[int, str]]:
    """Flag the ``any`` type wherever it appears (annotation, ``as any``, generic arg).

    ``predefined_type`` is a type node, never a string literal or identifier, so this is
    free of the false positives a ``: any`` substring would hit.
    """
    return [
        (_line(node), "any type") for node in _walk(root) if node.type == "predefined_type" and _text(node) == "any"
    ]


# ─── ts-suppression ───────────────────────────────────────────────────────────


def _check_suppression(root: Node) -> list[tuple[int, str]]:
    """Flag ``@ts-ignore`` / ``@ts-nocheck`` suppression comments (not ``@ts-expect-error``,
    which self-clears when no error exists)."""
    out: list[tuple[int, str]] = []
    for node in _walk(root):
        if node.type != "comment":
            continue
        body = _text(node)
        if "@ts-ignore" in body or "@ts-nocheck" in body:
            out.append((_line(node), "type-error suppression comment"))
    return out


# ─── react-hook-order ─────────────────────────────────────────────────────────


def _is_logical(node: Node) -> bool:
    """True for a ``&&`` / ``||`` short-circuit expression."""
    op = node.child_by_field_name("operator")
    return op is not None and _text(op) in ("&&", "||")


def _check_hook_order(root: Node) -> list[tuple[int, str]]:
    """Flag a ``use…()`` hook call reached through a conditional/loop within its component
    — i.e. not at the top level of the enclosing function."""
    out: list[tuple[int, str]] = []
    for node in _walk(root):
        if node.type != "call_expression":
            continue
        callee = node.child_by_field_name("function")
        if callee is None or callee.type != "identifier" or not _HOOK_NAME.match(_text(callee)):
            continue
        parent = node.parent
        while parent is not None and parent.type not in _FUNCTION_TYPES:
            if parent.type in _CONDITIONAL_TYPES or (parent.type == "binary_expression" and _is_logical(parent)):
                out.append((_line(node), f"conditional hook call: {_text(callee)}()"))
                break
            parent = parent.parent
    return out


# ─── react-index-key ──────────────────────────────────────────────────────────


def _second_map_param(func: Node) -> str | None:
    """If ``func`` is the callback of a ``.map(...)`` call, its index (2nd) parameter."""
    args = func.parent
    if args is None or args.type != "arguments":
        return None
    call = args.parent
    if call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    if callee is None or callee.type != "member_expression":
        return None
    if _text(callee.child_by_field_name("property")) != "map":
        return None
    names = _param_names(func)
    return names[1] if len(names) >= 2 else None


def _attr_expression(attr: Node) -> Node | None:
    """The inner expression of a ``key={…}`` JSX attribute value.

    A ``jsx_attribute`` carries its value as a ``jsx_expression`` named child (there is no
    ``value`` field); the braces are anonymous, so its first named child is the expression.
    """
    for child in attr.named_children:
        if child.type == "jsx_expression":
            inner = child.named_children
            return inner[0] if inner else None
    return None


def _check_index_key(root: Node) -> list[tuple[int, str]]:
    """Flag a JSX ``key`` bound to the array index of the enclosing ``.map`` callback."""
    out: list[tuple[int, str]] = []
    for node in _walk(root):
        if node.type != "jsx_attribute":
            continue
        name = node.named_children[0] if node.named_children else None
        if name is None or _text(name) != "key":
            continue
        expr = _attr_expression(node)
        if expr is None or expr.type != "identifier":
            continue
        func = _enclosing_function(node)
        if func is not None and _text(expr) == _second_map_param(func):
            out.append((_line(node), "list key bound to array index"))
    return out


# ─── react-inline-component ───────────────────────────────────────────────────


def _has_jsx_return(node: Node) -> bool:
    """True if a return within ``node`` yields JSX, not descending into nested functions."""
    for child in node.named_children:
        if child.type in _FUNCTION_TYPES:
            continue
        if child.type == "return_statement" and _returns_jsx_value(child.named_children):
            return True
        if _has_jsx_return(child):
            return True
    return False


def _returns_jsx_value(nodes: Sequence[Node]) -> bool:
    """True if the first node (a return argument / concise body) is JSX, unwrapping parens."""
    if not nodes:
        return False
    node = nodes[0]
    if node.type in _JSX_TYPES:
        return True
    if node.type == "parenthesized_expression":
        return _returns_jsx_value(node.named_children)
    return False


def _returns_jsx(func: Node) -> bool:
    """True if a function-like node renders JSX (concise arrow body or a ``return``)."""
    body = func.child_by_field_name("body")
    if body is None:
        return False
    if body.type in _JSX_TYPES or (body.type == "parenthesized_expression" and _returns_jsx_value(body.named_children)):
        return True
    return _has_jsx_return(body)


def _component_name(func: Node) -> str | None:
    """Capitalized name a function-like node defines a component under, else ``None``.

    A component is PascalCase — a lowercase ``renderRow`` helper that returns JSX is not
    flagged, keeping this to genuine nested *component* definitions.
    """
    if func.type == "function_declaration":
        name = _text(func.child_by_field_name("name"))
        return name if name[:1].isupper() else None
    parent = func.parent
    if parent is not None and parent.type == "variable_declarator":
        name = _text(parent.child_by_field_name("name"))
        return name if name[:1].isupper() else None
    return None


def _check_inline_component(root: Node) -> list[tuple[int, str]]:
    """Flag a component defined inside another component's body (remounts on every render)."""
    out: list[tuple[int, str]] = []
    for node in _walk(root):
        if node.type not in _FUNCTION_TYPES:
            continue
        name = _component_name(node)
        if name is None or not _returns_jsx(node):
            continue
        outer = _enclosing_function(node)
        if outer is not None and _returns_jsx(outer):
            out.append((_line(node), f"component {name} defined inside another component"))
    return out


# ─── ts-non-null-assertion ────────────────────────────────────────────────────


def _check_non_null_assertion(root: Node) -> list[tuple[int, str]]:
    """Flag the postfix non-null assertion ``x!`` — it silences the strict null check at
    compile time, asserting non-null without proof.

    ``non_null_expression`` is a distinct node from ``!x`` negation (``unary_expression``)
    and ``!=``/``!==`` comparison (``binary_expression``), so this has none of the false
    positives a ``!`` substring would hit.
    """
    return [(_line(node), "non-null assertion") for node in _walk(root) if node.type == "non_null_expression"]


_JS_AST_CHECKS: dict[str, JsAstCheck] = {
    "ts-any-type": _check_any_type,
    "ts-suppression": _check_suppression,
    "ts-non-null-assertion": _check_non_null_assertion,
    "react-hook-order": _check_hook_order,
    "react-index-key": _check_index_key,
    "react-inline-component": _check_inline_component,
}

# Exposed for the YAML loader to validate ``detect.ast`` names against.
VALID_JS_AST_CHECKS: frozenset[str] = frozenset(_JS_AST_CHECKS)


def run_js_ast_checks(names: Sequence[str], source: str, suffix: str) -> list[tuple[int, str]]:
    """Parse ``source`` (per ``suffix``) once and run the named checks, deduped/line-sorted.

    Unknown names and unsupported suffixes yield nothing (the loader validates names up
    front). Parsing never raises — tree-sitter produces an ``ERROR`` tree for malformed
    input — so detection cannot crash the scan.
    """
    try:
        root = _parse(source, suffix)
    except Exception:  # pragma: no cover - defensive: detection must never crash the scan
        return []
    if root is None:
        return []
    seen: set[tuple[int, str]] = set()
    out: list[tuple[int, str]] = []
    for name in names:
        check = _JS_AST_CHECKS.get(name)
        if check is None:
            continue
        for finding in check(root):
            if finding not in seen:
                seen.add(finding)
                out.append(finding)
    out.sort(key=lambda f: f[0])
    return out


def unknown_js_checks(names: Iterable[str]) -> list[str]:
    """Return the names that are not registered JS/TS checks (for validation messages)."""
    return [n for n in names if n not in _JS_AST_CHECKS]
