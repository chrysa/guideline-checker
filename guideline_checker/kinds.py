"""The MECHANISMS taxonomy — a finite set of generic *check kinds* (ADR D-0020).

ADR D-0016 draws the founding line of this tool: **mechanisms live in the engine,
values live in the host's prose.** A *kind* is a mechanism — a generic way to
*measure* something — that carries no version, threshold, or target name of its
own. The host's prose supplies those values; a kind only says *how* a rule is
checked, never *what* the check enforces.

This module names that finite set explicitly and classifies any rule into its
kind, so the mechanism layer is a first-class, inspectable concept rather than an
implicit consequence of which ``detect.*`` field a rule happens to use. It adds
no detection behaviour: classification is derived from the existing
``RuleDetector`` (loader) and the phrase-derived checks (checker), so the engine
runs exactly as before.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from guideline_checker.loader import RuleDetector


class CheckKind(StrEnum):
    """A generic, value-free way the engine can measure a rule.

    Every enforceable rule maps to exactly one kind; a rule that maps to none is
    ``ADVISORY`` — surfaced but not mechanically checkable (host prose the engine
    cannot turn into a measurement without more than it is given).
    """

    FORBIDDEN_PATTERN = "forbidden-pattern"  # a substring/regex forbidden on a source line
    FILE_CONTENT = "file-content"  # a whole-file content requirement or prohibition
    AST_STRUCTURE = "ast-structure"  # a structural fact checked on the parse tree
    CONTENT_SCAN = "content-scan"  # a named content scanner (e.g. entropy secrets)
    NUMERIC_THRESHOLD = "numeric-threshold"  # a measured metric compared to a threshold
    FILE_PRESENCE = "file-presence"  # a path that must (or must not) exist
    ADVISORY = "advisory"  # no mechanical kind — prose surfaced, never enforced


# One line per kind: what the mechanism measures. The *values* it measures against
# (which pattern, which metric, which threshold, which path) always come from the
# host's own prose or config — never from this table.
KIND_MEASURES: dict[CheckKind, str] = {
    CheckKind.FORBIDDEN_PATTERN: "a forbidden substring or regex appears on a source line",
    CheckKind.FILE_CONTENT: "a whole-file regex matches (or fails to match) a file's content",
    CheckKind.AST_STRUCTURE: "a structural fact holds on a file's parse tree",
    CheckKind.CONTENT_SCAN: "a named content scanner flags a line (e.g. high-entropy secret)",
    CheckKind.NUMERIC_THRESHOLD: "a measured metric (length, complexity, coverage) crosses a threshold",
    CheckKind.FILE_PRESENCE: "a required path exists, or a forbidden path is absent",
    CheckKind.ADVISORY: "no mechanical measurement — the rule is surfaced but never enforced",
}

# Phrase-derived rules carry no ``detect:`` block; the engine recognises their
# prose. These patterns classify such a rule into a metric/presence kind so the
# taxonomy covers phrase-detected rules too (kept in sync with checker.py).
_NUMERIC_PROSE = re.compile(
    r"\b(max(?:imum)?|min(?:imum)?|at least|no more than|coverage|complexity|length)\b.*\d",
    re.I,
)
_PRESENCE_PROSE = re.compile(r"\b(must (?:exist|be present|have)|require[sd]?|mandatory)\b", re.I)


def kind_of_detector(detector: RuleDetector | None) -> CheckKind | None:
    """Classify a declarative ``RuleDetector`` into its kind, or ``None`` if empty.

    Precedence follows how the checker dispatches: a structural (AST) or scanner
    mechanism is reported over a raw pattern one when a rule carries several.
    """
    if detector is None:
        return None
    if detector.ast_checks:
        return CheckKind.AST_STRUCTURE
    if detector.scan_checks:
        return CheckKind.CONTENT_SCAN
    if detector.file_regex or detector.require_regex:
        return CheckKind.FILE_CONTENT
    if detector.forbid or detector.forbid_regex:
        return CheckKind.FORBIDDEN_PATTERN
    return None


def kind_of_phrase(rule: str) -> CheckKind:
    """Classify a phrase-detected rule (one with no ``detect:`` block) into a kind."""
    if _NUMERIC_PROSE.search(rule):
        return CheckKind.NUMERIC_THRESHOLD
    if _PRESENCE_PROSE.search(rule):
        return CheckKind.FILE_PRESENCE
    # A phrase the checker recognises but that is neither a metric nor a presence
    # rule is a forbidden pattern (its phrase maps to substrings/regex).
    return CheckKind.FORBIDDEN_PATTERN
