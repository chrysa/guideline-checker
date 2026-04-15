"""Tests for the JSON reporter."""

from __future__ import annotations

import json
from pathlib import Path

from guideline_checker.checker import RuleResult, Violation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.json_reporter import JsonReporter


def _make_instruction(tmp_path: Path, apply_to: str = "**/*.py") -> InstructionFile:
    path = tmp_path / "test.instructions.md"
    path.write_text("# Test\n", encoding="utf-8")
    return InstructionFile(
        path=path,
        apply_to=apply_to,
        description="Test guideline",
        content="# Test\n",
        rules=["No print() calls"],
    )


def test_json_reporter_creates_file(tmp_path: Path) -> None:
    """Should create a JSON report file at the specified path."""
    instruction = _make_instruction(tmp_path)
    result = RuleResult(instruction=instruction, violations=[], files_checked=5)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    assert output.exists()


def test_json_reporter_valid_json(tmp_path: Path) -> None:
    """Output should be valid JSON."""
    instruction = _make_instruction(tmp_path)
    result = RuleResult(instruction=instruction, violations=[], files_checked=3)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_json_reporter_summary_stats(tmp_path: Path) -> None:
    """Summary should correctly aggregate counts across all results."""
    instruction = _make_instruction(tmp_path)
    violations = [
        Violation(file=tmp_path / "a.py", line_number=1, line_content="print()", rule="No print", severity="error"),
        Violation(file=tmp_path / "b.py", line_number=2, line_content="pprint()", rule="No pprint", severity="warning"),
        Violation(file=tmp_path / "c.py", line_number=3, line_content="# info", rule="Style", severity="info"),
    ]
    result = RuleResult(instruction=instruction, violations=violations, files_checked=10)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    summary = data["summary"]
    assert summary["files_checked"] == 10
    assert summary["total_violations"] == 3
    assert summary["errors"] == 1
    assert summary["warnings"] == 1
    assert summary["info"] == 1


def test_json_reporter_violations_in_output(tmp_path: Path) -> None:
    """Violations should appear in the rules section with correct fields."""
    instruction = _make_instruction(tmp_path)
    violation = Violation(
        file=tmp_path / "src" / "app.py",
        line_number=5,
        line_content='    print("hello")',
        rule="No print() calls",
        severity="warning",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    rule_entry = data["rules"][0]
    assert rule_entry["files_checked"] == 1
    assert len(rule_entry["violations"]) == 1
    v = rule_entry["violations"][0]
    assert v["severity"] == "warning"
    assert v["line"] == 5
    assert v["rule"] == "No print() calls"
    assert "app.py" in v["file"]


def test_json_reporter_relative_file_paths(tmp_path: Path) -> None:
    """Violation file paths should be relative to root when inside it."""
    instruction = _make_instruction(tmp_path)
    src_file = tmp_path / "src" / "module.py"
    violation = Violation(
        file=src_file,
        line_number=1,
        line_content="x = 1",
        rule="Some rule",
        severity="info",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    file_path = data["rules"][0]["violations"][0]["file"]
    assert not Path(file_path).is_absolute()
    assert "module.py" in file_path


def test_json_reporter_creates_parent_dirs(tmp_path: Path) -> None:
    """Should create parent directories if they don't exist."""
    instruction = _make_instruction(tmp_path)
    result = RuleResult(instruction=instruction, violations=[], files_checked=0)
    reporter = JsonReporter()
    output = tmp_path / "nested" / "deep" / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    assert output.exists()


def test_json_reporter_empty_results(tmp_path: Path) -> None:
    """Should produce valid output with empty results list."""
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["summary"]["total_violations"] == 0
    assert data["summary"]["files_checked"] == 0
    assert data["rules"] == []


def test_json_reporter_has_generated_at(tmp_path: Path) -> None:
    """Output should include a generated_at timestamp."""
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert "generated_at" in data
    assert data["generated_at"]


def test_json_reporter_instruction_metadata(tmp_path: Path) -> None:
    """Rule entries should include instruction metadata."""
    instruction = _make_instruction(tmp_path, apply_to="**/*.py")
    result = RuleResult(instruction=instruction, violations=[], files_checked=2)
    reporter = JsonReporter()
    output = tmp_path / "report.json"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    rule_entry = data["rules"][0]
    assert rule_entry["description"] == "Test guideline"
    assert rule_entry["apply_to"] == "**/*.py"
    assert "test.instructions.md" in rule_entry["instruction_file"]
