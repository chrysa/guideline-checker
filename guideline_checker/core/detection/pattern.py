"""Pattern-derived checks: substring/regex matching against source lines and
glob-based exclusion, feeding a rule's declarative or seed-derived
``RuleDetector`` (ADR D-0016: mechanisms live in the engine, values live in the
host's prose). The rule-phrase -> ``RuleDetector`` dispatch table itself lives
in :mod:`guideline_checker.core.derive.seed` — a heuristic, not a mechanism."""

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
        # Python 3.13+ `full_match` implements recursive ``**`` across directory
        # boundaries; `PurePath.match` treats ``**`` as a single ``*``, so a
        # pattern like ``**/dashboards/**/*.json`` misses a root-level
        # ``dashboards/core.json``. Prefer `full_match`, fall back to `match`
        # on 3.12 where it does not exist.
        if hasattr(relative, "full_match"):
            if relative.full_match(pat):
                return True
        elif relative.match(pat):
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


def _pattern_check_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    checks: tuple[PatternCheck, ...],
) -> list[Violation]:
    """Evaluate independent per-pattern ``PatternCheck``s against file lines.

    Unlike :func:`_per_line_violations` (which applies one shared
    ``match_in_comments`` from a merged ``RuleDetector`` to every pattern),
    each check here keeps its own ``match_in_comments`` and ``severity`` — a
    rule that arms both a comment-scoped pattern (e.g. ``TODO``) and a
    code-only pattern (e.g. ``assert``) at once must not let the code-only
    pattern match inside comments.

    One violation per line: the first matching check (in the seed table's
    family-priority order) wins and the rest are skipped for that line — same
    tie-break the pre-Task-3 phrase table used.
    """
    violations: list[Violation] = []
    for lineno, line in enumerate(lines, start=1):
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
                break
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


def _line_matches(line: str, pattern: str, *, match_in_comments: bool = False) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments by default)."""
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern.lower() in stripped.lower()
