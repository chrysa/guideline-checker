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

from guideline_checker.core.derive.seed import derive_seed_rules
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


# --- Task 6 fix round 1 (Finding 1): resolve_rule_detectors-filled primary
# detector must not be double-evaluated against the always-on supplementary
# seed check. See core/detection/__init__.py's _evaluate_rule and the Task 6
# controller ruling recorded there.


def test_seed_filled_primary_detector_does_not_duplicate_the_supplementary_check() -> None:
    """A rule resolve_rule_detectors filled from derive_seed_rules() (no YAML
    detector existed) must not be evaluated twice — once through its primary
    detector and once through the always-on supplementary seed check."""
    rule = "No print statements in production code"
    # Exactly what resolve_rule_detectors' cache-first pre-pass would fill in
    # for this rule, absent a cache hit (spec §3.3).
    primary_detector = derive_seed_rules(rule)
    assert primary_detector is not None

    lines = ['print("hello")']

    violations = _evaluate_rule(Path("dummy.py"), lines, rule, primary_detector)

    assert len(violations) == 1


def test_seed_filled_primary_detector_preserves_comment_fidelity_for_mixed_families() -> None:
    """ "No TODO comments and no print statements in production code" arms both
    the comment-scoped TODO family and the code-only print family. Once
    resolve_rule_detectors merges both into one primary RuleDetector (a single
    OR'd match_in_comments), evaluating that merged detector must not let the
    code-only print pattern leak into a comment line."""
    rule = "No TODO comments and no print statements in production code"
    primary_detector = derive_seed_rules(rule)
    assert primary_detector is not None
    assert primary_detector.match_in_comments is True  # merged flag, from the TODO family

    line = '# print("debugging note in a comment")'

    violations = _evaluate_rule(Path("dummy.py"), [line], rule, primary_detector)

    assert violations == []
