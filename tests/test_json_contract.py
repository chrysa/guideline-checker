"""The JSON report is a contract: a consumer pins a version, not a guess (ADR D-0022).

Standards Hub is meant to consume compliance results without reaching into the
engine. Without a declared version the only thing it could pin was the tool's git
tag, coupling it to every unrelated release. These tests hold the envelope's shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.json_reporter import SCHEMA_VERSION, JsonReporter


def _instruction(tmp_path: Path) -> InstructionFile:
    path = tmp_path / "python.instructions.md"
    path.write_text("# Test\n", encoding="utf-8")
    return InstructionFile(
        path=path,
        apply_to="**/*.py",
        description="Test guideline",
        content="# Test\n",
        rules=["No print() calls"],
    )


def _report(tmp_path: Path, results: list[RuleResult]) -> dict:
    out = tmp_path / "report.json"
    JsonReporter().write(results, out, tmp_path)
    return json.loads(out.read_text(encoding="utf-8"))


def _one_violation(tmp_path: Path) -> RuleResult:
    target = tmp_path / "sample.py"
    target.write_text("print('x')\n", encoding="utf-8")
    violation = Violation(
        file=target,
        line_number=1,
        line_content="print('x')",
        rule="No print() calls",
        severity="warning",
    )
    return RuleResult(instruction=_instruction(tmp_path), violations=[violation], files_checked=1)


def test_the_envelope_declares_a_schema_version(tmp_path: Path) -> None:
    assert _report(tmp_path, [])["schema_version"] == SCHEMA_VERSION


def test_the_schema_version_is_major_dot_minor() -> None:
    major, _, minor = SCHEMA_VERSION.partition(".")
    assert major.isdigit()
    assert minor.isdigit()


def test_the_envelope_keeps_the_fields_existing_consumers_read(tmp_path: Path) -> None:
    """Additive only — a contract that breaks its first consumer is not a contract."""
    assert {"generated_at", "project_root", "summary", "rules"} <= set(_report(tmp_path, []))


def test_the_summary_counts_every_severity_even_at_zero(tmp_path: Path) -> None:
    """An absent key reads as 'unknown'; an explicit zero reads as 'measured, none found'."""
    assert _report(tmp_path, [])["summary"] == {
        "files_checked": 0,
        "total_violations": 0,
        "errors": 0,
        "warnings": 0,
        "info": 0,
    }


def test_a_violation_carries_its_kind_and_fingerprint(tmp_path: Path) -> None:
    [rule] = _report(tmp_path, [_one_violation(tmp_path)])["rules"]
    [violation] = rule["violations"]
    assert {"severity", "file", "line", "content", "rule", "kind", "fingerprint"} <= set(violation)


def test_the_fingerprint_matches_the_one_the_baseline_computes(tmp_path: Path) -> None:
    """A result a consumer cannot join to the baseline cannot answer 'is this accepted debt?'."""
    from guideline_checker.baseline import fingerprint

    result = _one_violation(tmp_path)
    [rule] = _report(tmp_path, [result])["rules"]
    assert rule["violations"][0]["fingerprint"] == fingerprint(result.violations[0], tmp_path)


def test_a_phrase_detected_rule_still_reports_a_kind(tmp_path: Path) -> None:
    """Every rule reports exactly one kind — a blank would make the field unusable."""
    [rule] = _report(tmp_path, [_one_violation(tmp_path)])["rules"]
    assert rule["violations"][0]["kind"]


def test_each_rule_entry_names_its_source(tmp_path: Path) -> None:
    [rule] = _report(tmp_path, [_one_violation(tmp_path)])["rules"]
    assert rule["instruction_file"] == "python.instructions.md"
