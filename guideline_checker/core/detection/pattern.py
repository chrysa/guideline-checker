"""Pattern- and phrase-derived checks: substring/regex matching against source
lines, glob-based exclusion, and the rule-phrase -> ``PatternCheck`` dispatch
table (ADR D-0016: mechanisms live in the engine, values live in the host's
prose)."""

from __future__ import annotations

import fnmatch
import functools
import re
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from guideline_checker.core.detection import Violation
from guideline_checker.loader import RuleDetector

# Inline suppression marker — add this comment on any line to skip all rule checks
DISABLE_COMMENT = "guideline: disable"


class PatternCheck(NamedTuple):
    """A single pattern check derived from a rule sentence."""

    pattern: str
    severity: str
    match_in_comments: bool = False


def _is_excluded(file_path: Path, root: Path, patterns: list[str]) -> bool:
    """Return True if ``file_path`` (relative to ``root``) matches any exclude pattern.

    A pattern matches when the relative POSIX path equals it, lives beneath it
    (bare-directory exclusion, e.g. ``tests`` skips ``tests/a/b.py``), or matches
    it as a recursive glob (``**`` supported via :meth:`PurePath.full_match`,
    e.g. ``scripts/**/*.py`` or ``**/*.md``).
    """
    try:
        rel = file_path.relative_to(root).as_posix()
    except ValueError:
        return False
    rel_path = PurePosixPath(rel)
    for raw in patterns:
        pat = raw.strip().rstrip("/")
        if not pat:
            continue
        if rel == pat or rel.startswith(pat + "/"):
            return True
        if rel_path.full_match(pat):
            return True
    return False


def _split_patterns(pattern: str) -> list[str]:
    """Split comma-separated glob patterns, respecting brace groups ``{a,b}``.

    A plain ``split(",")`` would break patterns like ``{api,adminzone}/**/*.py``
    by splitting inside the braces.  This function only splits on commas that
    are *not* nested inside ``{…}``.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in pattern:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _expand_brace_pattern(pattern: str) -> list[str]:
    """Expand ``{a,b}/foo`` into ``[a/foo, b/foo]`` recursively.

    Only the innermost (non-nested) brace group is expanded per call;
    recursion handles nested braces and multiple brace groups.
    """
    m = re.search(r"\{([^{}]+)\}", pattern)
    if not m:
        return [pattern]
    before = pattern[: m.start()]
    after = pattern[m.end() :]
    expanded: list[str] = []
    for alt in m.group(1).split(","):
        expanded.extend(_expand_brace_pattern(before + alt.strip() + after))
    return expanded


def _matches_pattern(file_path: Path, root: Path, pattern: str) -> bool:
    """Check if a file path matches a glob pattern (relative to root).

    Supports ``**`` recursive wildcards via :meth:`pathlib.PurePath.match`
    with a fallback for root-level files (Python 3.12 compat).
    Comma-separated patterns are treated as alternatives (match any).
    Brace expansion ``{a,b}`` is supported (e.g. ``{api,adminzone}/**/*.py``).
    """
    try:
        relative = file_path.relative_to(root)
    except ValueError:
        return False

    # Split on commas NOT inside braces, then expand brace alternatives.
    raw_patterns = _split_patterns(pattern)
    patterns: list[str] = []
    for p in raw_patterns:
        patterns.extend(_expand_brace_pattern(p))

    for pat in patterns:
        if relative.match(pat):
            return True
        # Python 3.12: PurePath.match("**/*.ext") won't match root-level
        # files. Strip the leading **/ and try matching the filename.
        if pat.startswith("**/") and fnmatch.fnmatch(file_path.name, pat[3:]):
            return True
    return False


def _per_line_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Check per-line substring and regex patterns; return matching violations."""
    violations: list[Violation] = []
    regexes = tuple(_compile_regex(p) for p in detector.forbid_regex)
    for lineno, line in enumerate(lines, start=1):
        if DISABLE_COMMENT in line:
            continue
        matched = any(
            _line_matches(line, pat, match_in_comments=detector.match_in_comments) for pat in detector.forbid
        ) or _line_passes_regex(line, regexes, match_in_comments=detector.match_in_comments)
        if matched:
            violations.append(
                Violation(
                    file=file_path,
                    line_number=lineno,
                    line_content=line.strip()[:120],
                    rule=rule,
                    severity="warning",
                ),
            )
    return violations


def _file_regex_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Check whole-file (structural / multiline) regex patterns; return matching violations."""
    if not detector.file_regex:
        return []
    violations: list[Violation] = []
    content = "\n".join(lines)
    for pattern in detector.file_regex:
        for match in _compile_regex(pattern).finditer(content):
            lineno = content.count("\n", 0, match.start()) + 1
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if DISABLE_COMMENT in line:
                continue
            violations.append(
                Violation(
                    file=file_path,
                    line_number=lineno,
                    line_content=line.strip()[:120],
                    rule=rule,
                    severity="warning",
                ),
            )
    return violations


def _require_regex_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Flag a file that is *missing* a required pattern — the absence is the defect.

    Every other mechanism fires on something present, so a rule like "an HTML page
    must declare a viewport" or "the Makefile must define the mandatory targets"
    was inexpressible: `file_regex` says nothing when it finds nothing.

    A missing pattern has no line to point at, so the violation is anchored at line 1
    and carries the pattern as its content — a bare empty string would collapse the
    baseline fingerprint of two different missing requirements into one.
    """
    if not detector.require_regex:
        return []
    content = "\n".join(lines)
    return [
        Violation(
            file=file_path,
            line_number=1,
            line_content=f"missing: {pattern}"[:120],
            rule=rule,
            severity="warning",
        )
        for pattern in detector.require_regex
        if not _compile_regex(pattern).search(content)
    ]


def _line_passes_regex(line: str, regexes: tuple[re.Pattern[str], ...], *, match_in_comments: bool) -> bool:
    """Return True if any compiled regex matches the line (comment rules as for substrings)."""
    if not regexes:
        return False
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return any(rx.search(line) for rx in regexes)


@functools.cache
def _compile_regex(pattern: str) -> re.Pattern[str]:
    """Compile (and cache) a declarative-detector regex, case-insensitive and multiline."""
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


@functools.cache
def _build_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    """Build anti-pattern checks from rule text.

    Cached so that identical rules across multiple instruction sources are
    only compiled once — eliminates O(rules x files) redundant work.
    """
    checks: list[PatternCheck] = []
    checks.extend(_debug_output_checks(rule_lower))
    checks.extend(_exception_checks(rule_lower))
    checks.extend(_dangerous_builtin_checks(rule_lower))
    checks.extend(_import_checks(rule_lower))
    checks.extend(_annotation_checks(rule_lower))
    checks.extend(_hygiene_checks(rule_lower))
    checks.extend(_typescript_checks(rule_lower))
    checks.extend(_python_strict_checks(rule_lower))
    checks.extend(_security_checks(rule_lower))
    checks.extend(_docker_checks(rule_lower))
    checks.extend(_django_checks(rule_lower))
    return tuple(checks)


def _debug_output_checks(rule_lower: str) -> list[PatternCheck]:
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
    return checks


def _exception_checks(rule_lower: str) -> list[PatternCheck]:
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        return [PatternCheck("except:", "error")]
    return []


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


def _dangerous_builtin_checks(rule_lower: str) -> list[PatternCheck]:
    # Word-bounded: a rule like "no executable runtime" or "no evaluation of X"
    # must NOT be read as "no exec()" / "no eval()". A plain substring test
    # matched "no exec" inside "no executable" and flagged every JS
    # ``RegExp.prototype.exec()`` call across the fleet.
    checks: list[PatternCheck] = []
    if re.search(r"\bno eval\b", rule_lower):
        checks.append(PatternCheck("eval(", "error"))
    if re.search(r"\bno exec\b", rule_lower):
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


def _annotation_checks(_rule_lower: str) -> list[PatternCheck]:
    # Handled by _check_presence_rules (whole-file absence check) — no per-line pattern needed.
    return []


def _hygiene_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no todo", "no todos", "avoid todo")):
        checks.append(PatternCheck("TODO", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no fixme", "avoid fixme")):
        checks.append(PatternCheck("FIXME", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no hack", "avoid hack")):
        checks.append(PatternCheck("HACK", "warning", match_in_comments=True))
    if "no assert" in rule_lower and "test" not in rule_lower:
        checks.append(PatternCheck("assert ", "warning"))
    if any(phrase in rule_lower for phrase in ("no magic number", "no magic string", "magic number")):
        # Cannot detect statically without full AST, but flag obvious cases
        pass
    return checks


def _docker_checks(rule_lower: str) -> list[PatternCheck]:
    """Docker / container checks."""
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("run as non-root", "non-root user", "no root")):
        checks.append(PatternCheck("USER root", "error"))
    if "no latest tag" in rule_lower or "never use :latest" in rule_lower:
        checks.append(PatternCheck(":latest", "warning"))
    if "no add instruction" in rule_lower or "use copy not add" in rule_lower:
        checks.append(PatternCheck("\nADD ", "warning"))
    return checks


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
        checks.append(PatternCheck("0.0.0.0", "warning"))
    if "no shell=true" in rule_lower or "no shell injection" in rule_lower:
        checks.append(PatternCheck("shell=True", "error"))
    if "no pickle" in rule_lower:
        checks.append(PatternCheck("import pickle", "error"))
        checks.append(PatternCheck("pickle.load", "error"))
    return checks


def _django_checks(rule_lower: str) -> list[PatternCheck]:
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
    return checks


def _line_matches(line: str, pattern: str, *, match_in_comments: bool = False) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments by default)."""
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern.lower() in stripped.lower()
