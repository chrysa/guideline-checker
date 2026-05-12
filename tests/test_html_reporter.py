"""Tests for the HTML reporter."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.checker import RuleResult, Violation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.html import HtmlReporter


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


def test_html_reporter_creates_file(tmp_path: Path) -> None:
    """Should create an HTML report file."""
    instruction = _make_instruction(tmp_path)
    result = RuleResult(instruction=instruction, violations=[], files_checked=5)
    reporter = HtmlReporter()
    output = tmp_path / "report.html"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "Guideline Compliance Report" in content


def test_html_reporter_shows_violations(tmp_path: Path) -> None:
    """Should render violations in the HTML output."""
    instruction = _make_instruction(tmp_path)
    violation = Violation(
        file=tmp_path / "src" / "app.py",
        line_number=5,
        line_content='    print("hello")',
        rule="No print() calls",
        severity="warning",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    reporter = HtmlReporter()
    output = tmp_path / "report.html"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    content = output.read_text(encoding="utf-8")
    assert "WARNING" in content
    assert "app.py" in content


def test_html_reporter_pass_when_no_violations(tmp_path: Path) -> None:
    """Should show PASS badge when there are no violations."""
    instruction = _make_instruction(tmp_path)
    result = RuleResult(instruction=instruction, violations=[], files_checked=3)
    reporter = HtmlReporter()
    output = tmp_path / "report.html"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    content = output.read_text(encoding="utf-8")
    assert "PASS" in content


def test_html_reporter_info_violation(tmp_path: Path) -> None:
    """Violations with severity 'info' should render with badge-warning (only errors bump class)."""
    instruction = _make_instruction(tmp_path)
    violation = Violation(
        file=tmp_path / "src" / "util.py",
        line_number=2,
        line_content="from __future__ import annotations",
        rule="Use from __future__ import annotations",
        severity="info",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    reporter = HtmlReporter()
    output = tmp_path / "report.html"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    content = output.read_text(encoding="utf-8")
    assert "INFO" in content


def test_html_reporter_violation_outside_root(tmp_path: Path) -> None:
    """Violations whose file is outside root should use absolute path (fallback branch)."""
    instruction = _make_instruction(tmp_path)
    external = tmp_path.parent / "external_file.py"
    external.write_text("print('hi')\n", encoding="utf-8")
    violation = Violation(
        file=external,
        line_number=1,
        line_content="print('hi')",
        rule="No print() calls",
        severity="warning",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    reporter = HtmlReporter()
    output = tmp_path / "report.html"
    reporter.write(results=[result], output_path=output, root=tmp_path)
    content = output.read_text(encoding="utf-8")
    assert "external_file.py" in content
