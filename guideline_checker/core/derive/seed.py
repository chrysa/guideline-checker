"""The seed translator: prose -> a heuristic, in-memory ``RuleDetector``.

Demotes the checker's old rule-phrase dispatch table (ADR D-0016: mechanisms
live in the engine, values live in the host's prose) from a first-class
per-line check engine to a *heuristic* — the fast, free, deterministic first
step of the generation loop that Task 4/6 build on top of ``core/derive``. A
rule whose prose this table recognises gets an armed ``RuleDetector`` with no
LLM involved; a rule it cannot map returns ``None`` so the caller can escalate
(to an LLM, if ``[workshop]`` is installed) or leave the rule advisory.

Each family function recognises one cluster of related phrases and returns a
populated ``RuleDetector`` (pattern-kind fields only: ``forbid`` /
``match_in_comments``) or ``None`` when it finds nothing. ``derive_seed_rules``
runs every family and merges their results — a rule's prose can trip more than
one family at once (e.g. "no print calls and no eval and no any"), and losing
that aggregation would silently drop coverage the old phrase table had.
"""

from __future__ import annotations

import functools
import re

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


def _debug_output_checks(rule_lower: str) -> RuleDetector | None:
    forbid: list[str] = []
    if "no print" in rule_lower or "print()" in rule_lower or "never use print" in rule_lower:
        forbid.append("print(")
    if "no pprint" in rule_lower or "pprint()" in rule_lower:
        forbid.append("pprint(")
    if any(phrase in rule_lower for phrase in ("no console.log", "no `console.log`", "never console.log")):
        forbid.append("console.log(")
    if any(phrase in rule_lower for phrase in ("no console.debug", "no `console.debug`")):
        forbid.append("console.debug(")
    if any(phrase in rule_lower for phrase in ("no debugger", "no `debugger`")):
        forbid.append("debugger")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


def _exception_checks(rule_lower: str) -> RuleDetector | None:
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        return RuleDetector(forbid=("except:",))
    return None


def _dangerous_builtin_checks(rule_lower: str) -> RuleDetector | None:
    # Word-bounded: a rule like "no executable runtime" or "no evaluation of X"
    # must NOT be read as "no exec()" / "no eval()". A plain substring test
    # matched "no exec" inside "no executable" and flagged every JS
    # ``RegExp.prototype.exec()`` call across the fleet.
    forbid: list[str] = []
    if re.search(r"\bno eval\b", rule_lower):
        forbid.append("eval(")
    if re.search(r"\bno exec\b", rule_lower):
        forbid.append("exec(")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


def _import_checks(rule_lower: str) -> RuleDetector | None:
    forbid: list[str] = []
    if any(phrase in rule_lower for phrase in ("no import *", "no wildcard import", "no star import")):
        forbid.append("import *")
    if any(phrase in rule_lower for phrase in ("no relative import", "absolute import")):
        forbid.append("from . import")
        forbid.append("from .. import")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


def _annotation_checks(_rule_lower: str) -> RuleDetector | None:
    # Handled by _check_presence_rules (whole-file absence check) — no per-line pattern needed.
    return None


def _hygiene_checks(rule_lower: str) -> RuleDetector | None:
    comment_forbid: list[str] = []
    forbid: list[str] = []
    if any(phrase in rule_lower for phrase in ("no todo", "no todos", "avoid todo")):
        comment_forbid.append("TODO")
    if any(phrase in rule_lower for phrase in ("no fixme", "avoid fixme")):
        comment_forbid.append("FIXME")
    if any(phrase in rule_lower for phrase in ("no hack", "avoid hack")):
        comment_forbid.append("HACK")
    if "no assert" in rule_lower and "test" not in rule_lower:
        forbid.append("assert ")
    all_forbid = comment_forbid + forbid
    if not all_forbid:
        return None
    return RuleDetector(forbid=tuple(all_forbid), match_in_comments=bool(comment_forbid))


def _docker_checks(rule_lower: str) -> RuleDetector | None:
    """Docker / container checks."""
    forbid: list[str] = []
    if any(phrase in rule_lower for phrase in ("run as non-root", "non-root user", "no root")):
        forbid.append("USER root")
    if "no latest tag" in rule_lower or "never use :latest" in rule_lower:
        forbid.append(":latest")
    if "no add instruction" in rule_lower or "use copy not add" in rule_lower:
        forbid.append("\nADD ")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


def _typescript_checks(rule_lower: str) -> RuleDetector | None:
    """TypeScript / React anti-pattern checks."""
    comment_forbid: list[str] = []
    forbid: list[str] = []
    if "no any" in rule_lower or "no `any`" in rule_lower or "avoid any" in rule_lower:
        forbid.append(": any")
        forbid.append("as any")
    if "no ts-ignore" in rule_lower or "no @ts-ignore" in rule_lower:
        comment_forbid.append("@ts-ignore")
    if "no ts-nocheck" in rule_lower or "no @ts-nocheck" in rule_lower:
        comment_forbid.append("@ts-nocheck")
    if "no console.log" in rule_lower:
        forbid.append("console.log(")
    if "no console.debug" in rule_lower:
        forbid.append("console.debug(")
    if "no console.warn" in rule_lower:
        forbid.append("console.warn(")
    if "no inline style" in rule_lower or "no inline styles" in rule_lower:
        forbid.append("style={{")
    all_forbid = comment_forbid + forbid
    if not all_forbid:
        return None
    return RuleDetector(forbid=tuple(all_forbid), match_in_comments=bool(comment_forbid))


def _python_strict_checks(rule_lower: str) -> RuleDetector | None:
    """Strict Python quality checks."""
    comment_forbid: list[str] = []
    forbid: list[str] = []
    if "no global" in rule_lower and "global statement" in rule_lower:
        forbid.append("global ")
    if "no pass in except" in rule_lower or "no silent exception" in rule_lower:
        forbid.append("except:")
    if "no mutable default" in rule_lower:
        forbid.append("=[]")
        forbid.append("={}")
    if "no type: ignore" in rule_lower or "no type:ignore" in rule_lower:
        comment_forbid.append("type: ignore")
        comment_forbid.append("type:ignore")
    all_forbid = comment_forbid + forbid
    if not all_forbid:
        return None
    return RuleDetector(forbid=tuple(all_forbid), match_in_comments=bool(comment_forbid))


def _security_checks(rule_lower: str) -> RuleDetector | None:
    """Security-oriented checks (OWASP-aligned)."""
    forbid: list[str] = []
    if "no hardcoded url" in rule_lower or "no hardcoded urls" in rule_lower:
        forbid.append("http://")
        forbid.append("https://")
    if "no hardcoded ip" in rule_lower:
        forbid.append("127.0.0.1")
        forbid.append("0.0.0.0")
    if "no shell=true" in rule_lower or "no shell injection" in rule_lower:
        forbid.append("shell=True")
    if "no pickle" in rule_lower:
        forbid.append("import pickle")
        forbid.append("pickle.load")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


def _django_checks(rule_lower: str) -> RuleDetector | None:
    """Django / DRF anti-pattern checks (settings hardening + ORM safety).

    The markdown rule loader strips underscores from rule text (``ALLOWED_HOSTS``
    becomes ``allowedhosts``), so trigger phrases are matched against an
    underscore-stripped copy. The emitted patterns keep underscores because they
    are matched against source lines, which retain them.
    """
    forbid: list[str] = []
    compact = rule_lower.replace("_", "")
    if "no debug = true" in compact or "debug must be false" in compact:
        forbid.append("debug = true")
        forbid.append("debug=true")
    if "no wildcard allowedhosts" in compact or "allowedhosts wildcard" in compact:
        forbid.append('allowed_hosts = ["*"')
        forbid.append("allowed_hosts = ['*'")
        forbid.append('allowed_hosts=["*"')
        forbid.append("allowed_hosts=['*'")
    if "no corsallowall" in compact or "corsallowallorigins" in compact:
        forbid.append("cors_allow_all_origins = true")
        forbid.append("cors_allow_all_origins=true")
    if "no raw sql" in compact or "no .raw(" in compact or "no queryset.raw" in compact:
        forbid.append(".raw(")
        forbid.append(".extra(")
    if "no hardcoded secretkey" in compact or "secretkey from env" in compact:
        forbid.append('secret_key = "')
        forbid.append("secret_key = '")
    return RuleDetector(forbid=tuple(forbid)) if forbid else None


@functools.cache
def derive_seed_rules(prose_rule: str) -> RuleDetector | None:
    """Turn a recognised prose sentence into an in-memory pattern detector.

    Returns ``None`` when no phrase family in the table recognises the sentence —
    the caller (core/derive, Task 4) then falls back to the LLM path if [workshop]
    is installed, or leaves the rule advisory otherwise.

    Runs every family (rather than stopping at the first match) and merges their
    ``forbid`` patterns: a single rule's prose can trip more than one family
    (e.g. "no print calls and no eval and no any" arms both the debug-output and
    dangerous-builtin families), and short-circuiting on the first match would
    silently lose the phrase table's original multi-family coverage.
    """
    rule_lower = prose_rule.lower()
    forbid: list[str] = []
    match_in_comments = False
    for family in (
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
    ):
        detector = family(rule_lower)
        if detector is None:
            continue
        forbid.extend(detector.forbid)
        match_in_comments = match_in_comments or detector.match_in_comments
    if not forbid:
        return None
    return RuleDetector(forbid=tuple(dict.fromkeys(forbid)), match_in_comments=match_in_comments)


# ``RuleDetector`` carries no severity of its own — see its docstring in
# loader.py: "the detector inherits the rule's own severity". A YAML rule gets
# that severity from its own ``severity:`` field; a markdown/phrase-derived rule
# has none, so the old phrase table baked a severity into each pattern itself
# (``eval(`` was "error", ``print(`` was "warning"). Merging every family's
# patterns into one detector (above) loses that per-pattern distinction unless
# something recovers it — this table is that something, consulted only for
# seed-derived violations (declared detectors are unaffected).
_ERROR_PATTERNS = frozenset(
    {
        "except:",
        "eval(",
        "exec(",
        "import *",
        "USER root",
        ": any",
        "as any",
        "@ts-ignore",
        "@ts-nocheck",
        "global ",
        "type: ignore",
        "type:ignore",
        "shell=True",
        "import pickle",
        "pickle.load",
        "debug = true",
        "debug=true",
        'allowed_hosts = ["*"',
        "allowed_hosts = ['*'",
        'allowed_hosts=["*"',
        "allowed_hosts=['*'",
        "cors_allow_all_origins = true",
        "cors_allow_all_origins=true",
        'secret_key = "',
        "secret_key = '",
    }
)
_INFO_PATTERNS = frozenset({"https://"})


def seed_violation_severity(matched_forbid: tuple[str, ...], line_content: str) -> str:
    """Recover the old phrase table's per-pattern severity for a seed-derived violation.

    ``matched_forbid`` is a detector's ``forbid`` tuple, in the phrase table's
    original family-priority order. Scans it for the first pattern present on
    ``line_content`` and returns that pattern's severity — same tie-break the old
    per-line loop used (``break`` at the first matching check).
    """
    low = line_content.lower()
    for pattern in matched_forbid:
        if pattern.lower() not in low:
            continue
        if pattern in _INFO_PATTERNS:
            return "info"
        if pattern in _ERROR_PATTERNS:
            return "error"
        return "warning"
    return "warning"
