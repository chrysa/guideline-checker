"""Tests for the baseline / incremental-adoption support (L2.2)."""

from __future__ import annotations

import json
from pathlib import Path

from guideline_checker.baseline import (
    apply_baseline,
    collect_fingerprints,
    fingerprint,
    load_baseline,
    write_baseline,
)
from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.loader import InstructionFile


def _instruction(name: str = "rules") -> InstructionFile:
    return InstructionFile(
        path=Path(f".github/instructions/{name}.md"),
        apply_to="**/*.py",
        description=name,
        content="",
        rules=[],
    )


def _violation(
    file: str = "src/app.py",
    line_number: int = 10,
    line_content: str = "assert x",
    rule: str = "no-assert",
    severity: str = "error",
) -> Violation:
    return Violation(
        file=Path(file),
        line_number=line_number,
        line_content=line_content,
        rule=rule,
        severity=severity,
    )


def _result(violations: list[Violation]) -> RuleResult:
    return RuleResult(instruction=_instruction(), violations=violations, files_checked=1)


class TestFingerprint:
    def test_is_line_number_independent(self, tmp_path: Path) -> None:
        """Same rule/file/content on different lines yields the same fingerprint."""
        a = _violation(line_number=10)
        b = _violation(line_number=999)
        assert fingerprint(a, tmp_path) == fingerprint(b, tmp_path)

    def test_ignores_surrounding_whitespace(self, tmp_path: Path) -> None:
        a = _violation(line_content="    assert x")
        b = _violation(line_content="assert x  ")
        assert fingerprint(a, tmp_path) == fingerprint(b, tmp_path)

    def test_differs_on_rule(self, tmp_path: Path) -> None:
        a = _violation(rule="no-assert")
        b = _violation(rule="no-print")
        assert fingerprint(a, tmp_path) != fingerprint(b, tmp_path)

    def test_differs_on_file(self, tmp_path: Path) -> None:
        a = _violation(file="src/a.py")
        b = _violation(file="src/b.py")
        assert fingerprint(a, tmp_path) != fingerprint(b, tmp_path)

    def test_differs_on_content(self, tmp_path: Path) -> None:
        a = _violation(line_content="assert x")
        b = _violation(line_content="assert y")
        assert fingerprint(a, tmp_path) != fingerprint(b, tmp_path)

    def test_absolute_and_relative_paths_match(self, tmp_path: Path) -> None:
        """A violation carrying an absolute path fingerprints like its repo-relative twin."""
        rel = _violation(file="src/app.py")
        absolute = _violation(file=str(tmp_path / "src" / "app.py"))
        assert fingerprint(rel, tmp_path) == fingerprint(absolute, tmp_path)


class TestWriteLoadRoundTrip:
    def test_round_trip(self, tmp_path: Path) -> None:
        results = [_result([_violation(rule="no-assert"), _violation(rule="no-print")])]
        path = tmp_path / "baseline.json"
        count = write_baseline(results, tmp_path, path)
        assert count == 2
        assert load_baseline(path) == collect_fingerprints(results, tmp_path)

    def test_output_is_deterministic_and_sorted(self, tmp_path: Path) -> None:
        """Two writes of the same result set produce byte-identical files (diff-stable)."""
        results = [_result([_violation(rule="z-rule"), _violation(rule="a-rule")])]
        p1 = tmp_path / "b1.json"
        p2 = tmp_path / "b2.json"
        write_baseline(results, tmp_path, p1)
        write_baseline(results, tmp_path, p2)
        assert p1.read_text() == p2.read_text()
        payload = json.loads(p1.read_text())
        assert payload["fingerprints"] == sorted(payload["fingerprints"])

    def test_load_missing_key_is_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        assert load_baseline(path) == set()


class TestApplyBaseline:
    def test_all_baselined_leaves_zero_new(self, tmp_path: Path) -> None:
        results = [_result([_violation(rule="no-assert"), _violation(rule="no-print")])]
        baseline = collect_fingerprints(results, tmp_path)
        outcome = apply_baseline(results, baseline, tmp_path)
        assert outcome.new_count == 0
        assert outcome.baselined_count == 2
        assert sum(len(r.violations) for r in outcome.results) == 0

    def test_new_violation_is_reported(self, tmp_path: Path) -> None:
        known = _result([_violation(rule="no-assert")])
        baseline = collect_fingerprints([known], tmp_path)
        introduced = _result([_violation(rule="no-assert"), _violation(rule="no-print")])
        outcome = apply_baseline([introduced], baseline, tmp_path)
        assert outcome.new_count == 1
        assert outcome.baselined_count == 1
        kept = [v for r in outcome.results for v in r.violations]
        assert [v.rule for v in kept] == ["no-print"]

    def test_line_drift_does_not_resurface(self, tmp_path: Path) -> None:
        """An edit that shifts a baselined violation's line keeps it baselined."""
        original = _result([_violation(line_number=10)])
        baseline = collect_fingerprints([original], tmp_path)
        drifted = _result([_violation(line_number=42)])  # same content, moved down
        outcome = apply_baseline([drifted], baseline, tmp_path)
        assert outcome.new_count == 0
        assert outcome.baselined_count == 1

    def test_empty_baseline_keeps_everything(self, tmp_path: Path) -> None:
        results = [_result([_violation(), _violation(rule="no-print")])]
        outcome = apply_baseline(results, set(), tmp_path)
        assert outcome.new_count == 2
        assert outcome.baselined_count == 0

    def test_preserves_result_metadata(self, tmp_path: Path) -> None:
        results = [_result([_violation()])]
        outcome = apply_baseline(results, set(), tmp_path)
        assert outcome.results[0].instruction is results[0].instruction
        assert outcome.results[0].files_checked == 1
