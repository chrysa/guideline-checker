"""The seed translator: prose -> a heuristic, in-memory ``RuleDetector``.

Demotes the checker's old rule-phrase dispatch table (ADR D-0016: mechanisms
live in the engine, values live in the host's prose) from a first-class
per-line check engine to a *heuristic* — the fast, free, deterministic first
step of the generation loop that Task 4/6 build on top of ``core/derive``. A
rule whose prose this table recognises gets an armed ``RuleDetector`` with no
LLM involved; a rule it cannot map returns ``None`` so the caller can escalate
(to an LLM, if ``[workshop]`` is installed) or leave the rule advisory.

Each family function recognises one cluster of related phrases and returns a
tuple of ``PatternCheck`` (pattern + its own ``severity`` / ``match_in_comments``
— the same per-pattern metadata the pre-Task-3 phrase table carried) or an
empty tuple when it finds nothing. ``derive_seed_pattern_checks`` runs every
family and aggregates their results — a rule's prose can trip more than one
family at once (e.g. "no print calls and no eval and no any"), and losing that
aggregation would silently drop coverage the old phrase table had.

``derive_seed_rules`` is a thin wrapper around ``derive_seed_pattern_checks``
that merges the per-pattern checks into a single ``RuleDetector``. This
collapses per-pattern ``severity``/``match_in_comments`` into one shared value
— fine for its two callers (``proposer.py``, ``rule_health.py``), which only
check truthiness or read ``.forbid`` and never evaluate the detector per-line.
Callers that *do* evaluate per-line (``core/detection/__init__.py``) must use
``derive_seed_pattern_checks`` directly to keep each pattern's own severity and
comment scope — see that function's docstring for why.
"""

from __future__ import annotations

import functools
import re

from guideline_checker.core.detection.pattern import PatternCheck
from guideline_checker.loader import RuleDetector

# Text-only recognition for "no hardcoded credential/secret/API key" rules. The
# scan itself (entropy-based, not a naive substring) lives in
# core/detection/scanners.py / core/detection/__init__.py's
# _credential_scan_violations — this is only the boolean gate.
_CREDENTIAL_TRIGGERS = ("no hardcoded", "hardcoded api", "hardcoded secret", "never hardcode", "all via env")
_CREDENTIAL_KEYWORDS = ("secret", "password", "credential", "key", "token", "api key", "api_key")


def _is_hardcoded_credential_rule(rule_lower: str) -> bool:
    """True when a rule forbids hardcoded credentials/secrets/API keys."""
    return any(t in rule_lower for t in _CREDENTIAL_TRIGGERS) and any(kw in rule_lower for kw in _CREDENTIAL_KEYWORDS)


@functools.lru_cache(maxsize=512)
def _mentions(rule_lower: str, phrase: str) -> bool:
    """True when ``phrase`` appears in ``rule_lower`` as whole words.

    A plain substring test makes short phrases bleed into longer words: prose such
    as "no executable runtime" contains "no exec", which used to arm an ``exec(``
    detector and flag every JavaScript ``RegExp.exec()`` call. Anchoring on word
    boundaries keeps the phrase table honest — the rule must actually name the
    construct it forbids.
    """
    return re.search(rf"\b{re.escape(phrase)}\b", rule_lower) is not None


def _debug_output_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    checks: list[PatternCheck] = []
    if "no print" in rule_lower or "print()" in rule_lower or "never use print" in rule_lower:
        checks.append(PatternCheck("print(", "warning"))
    if "no pprint" in rule_lower or "pprint()" in rule_lower:
        checks.append(PatternCheck("pprint(", "warning"))
    if any(phrase in rule_lower for phrase in ("no console.log", "no `console.log`", "never console.log")):
        checks.append(PatternCheck("console.log(", "warning"))
    if any(phrase in rule_lower for phrase in ("no console.debug", "no `console.debug`")):
        checks.append(PatternCheck("console.debug(", "warning"))
    if any(phrase in rule_lower for phrase in ("no debugger", "no `debugger`")):
        checks.append(PatternCheck("debugger", "warning"))
    return tuple(checks)


def _exception_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        return (PatternCheck("except:", "error"),)
    return ()


def _dangerous_builtin_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    # Word-bounded: a rule like "no executable runtime" or "no evaluation of X"
    # must NOT be read as "no exec()" / "no eval()". A plain substring test
    # matched "no exec" inside "no executable" and flagged every JS
    # ``RegExp.prototype.exec()`` call across the fleet.
    checks: list[PatternCheck] = []
    if re.search(r"\bno eval\b", rule_lower):
        checks.append(PatternCheck("eval(", "error"))
    if re.search(r"\bno exec\b", rule_lower):
        checks.append(PatternCheck("exec(", "error"))
    return tuple(checks)


def _import_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no import *", "no wildcard import", "no star import")):
        checks.append(PatternCheck("import *", "error"))
    if any(phrase in rule_lower for phrase in ("no relative import", "absolute import")):
        checks.append(PatternCheck("from . import", "warning"))
        checks.append(PatternCheck("from .. import", "warning"))
    return tuple(checks)


def _annotation_checks(_rule_lower: str) -> tuple[PatternCheck, ...]:
    # Handled by _check_presence_rules (whole-file absence check) — no per-line pattern needed.
    return ()


def _hygiene_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no todo", "no todos", "avoid todo")):
        checks.append(PatternCheck("TODO", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no fixme", "avoid fixme")):
        checks.append(PatternCheck("FIXME", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no hack", "avoid hack")):
        checks.append(PatternCheck("HACK", "warning", match_in_comments=True))
    if "no assert" in rule_lower and "test" not in rule_lower:
        checks.append(PatternCheck("assert ", "warning"))
    return tuple(checks)


def _docker_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    """Docker / container checks."""
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("run as non-root", "non-root user", "no root")):
        checks.append(PatternCheck("USER root", "error"))
    if "no latest tag" in rule_lower or "never use :latest" in rule_lower:
        checks.append(PatternCheck(":latest", "warning"))
    if "no add instruction" in rule_lower or "use copy not add" in rule_lower:
        checks.append(PatternCheck("\nADD ", "warning"))
    return tuple(checks)


def _typescript_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
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
    return tuple(checks)


def _python_strict_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
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
    return tuple(checks)


def _security_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    """Security-oriented checks (OWASP-aligned)."""
    checks: list[PatternCheck] = []
    if "no hardcoded url" in rule_lower or "no hardcoded urls" in rule_lower:
        checks.append(PatternCheck("http://", "warning"))
        checks.append(PatternCheck("https://", "info"))
    if "no hardcoded ip" in rule_lower:
        checks.append(PatternCheck("127.0.0.1", "warning"))
        checks.append(PatternCheck("0.0.0.0", "warning"))
    if "no shell=true" in rule_lower or "no shell injection" in rule_lower:
        checks.append(PatternCheck("shell=True", "error"))
    if "no pickle" in rule_lower:
        checks.append(PatternCheck("import pickle", "error"))
        checks.append(PatternCheck("pickle.load", "error"))
    return tuple(checks)


def _django_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    """Django / DRF anti-pattern checks (settings hardening + ORM safety).

    The markdown rule loader strips underscores from rule text (``ALLOWED_HOSTS``
    becomes ``allowedhosts``), so trigger phrases are matched against an
    underscore-stripped copy. The emitted patterns keep underscores because they
    are matched against source lines, which retain them.
    """
    checks: list[PatternCheck] = []
    compact = rule_lower.replace("_", "")
    if "no debug = true" in compact or "debug must be false" in compact:
        checks.append(PatternCheck("debug = true", "error"))
        checks.append(PatternCheck("debug=true", "error"))
    if "no wildcard allowedhosts" in compact or "allowedhosts wildcard" in compact:
        checks.append(PatternCheck('allowed_hosts = ["*"', "error"))
        checks.append(PatternCheck("allowed_hosts = ['*'", "error"))
        checks.append(PatternCheck('allowed_hosts=["*"', "error"))
        checks.append(PatternCheck("allowed_hosts=['*'", "error"))
    if "no corsallowall" in compact or "corsallowallorigins" in compact:
        checks.append(PatternCheck("cors_allow_all_origins = true", "error"))
        checks.append(PatternCheck("cors_allow_all_origins=true", "error"))
    if "no raw sql" in compact or "no .raw(" in compact or "no queryset.raw" in compact:
        checks.append(PatternCheck(".raw(", "warning"))
        checks.append(PatternCheck(".extra(", "warning"))
    if "no hardcoded secretkey" in compact or "secretkey from env" in compact:
        checks.append(PatternCheck('secret_key = "', "error"))
        checks.append(PatternCheck("secret_key = '", "error"))
    return tuple(checks)


_FAMILIES = (
    _debug_output_checks,
    _exception_checks,
    _dangerous_builtin_checks,
    _import_checks,
    _annotation_checks,
    _hygiene_checks,
    _docker_checks,
    _typescript_checks,
    _python_strict_checks,
    _security_checks,
    _django_checks,
)


@functools.cache
def derive_seed_pattern_checks(prose_rule: str) -> tuple[PatternCheck, ...]:
    """Turn a recognised prose sentence into its per-pattern ``PatternCheck``s.

    Returns an empty tuple when no phrase family in the table recognises the
    sentence — the caller (core/derive, Task 4) then falls back to the LLM path
    if [workshop] is installed, or leaves the rule advisory otherwise.

    Runs every family (rather than stopping at the first match) and aggregates
    their checks: a single rule's prose can trip more than one family (e.g.
    "no print calls and no eval and no any" arms both the debug-output and
    dangerous-builtin families), and short-circuiting on the first match would
    silently lose the phrase table's original multi-family coverage.

    Each returned ``PatternCheck`` keeps its own ``severity`` and
    ``match_in_comments`` — callers that evaluate per-line (see
    :func:`core.detection.pattern._pattern_check_violations`) must honour these
    per-pattern, not collapse them into one shared value the way a single
    ``RuleDetector`` would (that merge is exactly what :func:`derive_seed_rules`
    below does, and is only safe for callers that never evaluate per-line).

    Deduplicated by pattern text, first occurrence wins (family-priority
    order), matching the old phrase table's tie-break.
    """
    rule_lower = prose_rule.lower()
    checks: list[PatternCheck] = []
    for family in _FAMILIES:
        checks.extend(family(rule_lower))
    seen: set[str] = set()
    deduped: list[PatternCheck] = []
    for check in checks:
        if check.pattern in seen:
            continue
        seen.add(check.pattern)
        deduped.append(check)
    return tuple(deduped)


@functools.cache
def derive_seed_rules(prose_rule: str) -> RuleDetector | None:
    """Turn a recognised prose sentence into an in-memory pattern detector.

    A thin wrapper around :func:`derive_seed_pattern_checks` that merges its
    per-pattern checks into a single ``RuleDetector``. This collapses each
    pattern's own ``severity``/``match_in_comments`` into one shared value —
    acceptable here because this function's only two callers
    (``proposer.py``, ``rule_health.py``) only check truthiness or read
    ``.forbid``; neither evaluates the detector per-line, so the coalesced view
    costs them nothing. A caller that *does* evaluate per-line must call
    :func:`derive_seed_pattern_checks` directly instead (see
    ``core/detection/__init__.py``'s ``_evaluate_rule``) to avoid re-introducing
    the ``match_in_comments``/severity fidelity bugs this split fixes.
    """
    checks = derive_seed_pattern_checks(prose_rule)
    if not checks:
        return None
    forbid = tuple(check.pattern for check in checks)
    match_in_comments = any(check.match_in_comments for check in checks)
    return RuleDetector(forbid=forbid, match_in_comments=match_in_comments)
