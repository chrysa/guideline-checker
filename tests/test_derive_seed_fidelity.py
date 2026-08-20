"""Regression tests for two per-pattern fidelity bugs introduced when the phrase
table was demoted to a heuristic (``core/derive/seed.py``, see Task 3):

1. ``match_in_comments`` collapsed from a per-pattern flag to a single OR'd
   flag on the merged ``RuleDetector``. A rule that arms both a comment-scoped
   family (e.g. TODO) and a code-only family (e.g. ``assert``) at once let the
   code-only pattern match inside comments too.
2. Severity recovery (``seed_violation_severity``) re-scanned
   ``Violation.line_content``, which ``_per_line_violations`` truncates to
   ``line.strip()[:120]``. A pattern occurring past column 120 was invisible to
   the lookup and silently downgraded to "warning".

Both are exercised through ``_evaluate_rule`` — the real per-file evaluation
path — rather than in isolation, so a fix only proven at the translator level
without wiring wouldn't pass these.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import _evaluate_rule


def test_code_only_pattern_does_not_leak_into_comments_when_rule_also_arms_a_comment_pattern() -> None:
    """ "No TODOs and no assert statements outside code" arms both the
    comment-scoped TODO check and the code-only ``assert`` check. The
    ``assert`` check must not fire on a comment line."""
    rule = "No TODOs and no assert statements outside code"
    lines = [
        "# assert x is None  (documented, not code)",
        "y = 1",
    ]

    violations = _evaluate_rule(Path("dummy.py"), lines, rule)

    assert violations == []


def test_severity_is_recovered_even_when_the_matched_pattern_is_past_column_120() -> None:
    """A late-occurring ``eval(`` (past the 120-char line_content truncation)
    must still be reported as "error", not silently downgraded to "warning"."""
    rule = "no eval in application code"
    line = "x" * 130 + "eval("

    violations = _evaluate_rule(Path("dummy.py"), [line], rule)

    assert len(violations) == 1
    assert violations[0].severity == "error"
