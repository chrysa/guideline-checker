"""Tests for the Markdown reporter."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.markdown import MarkdownReporter


def _make_instruction(tmp_path: Path) -> InstructionFile:
    p = tmp_path / "python.instructions.md"
    p.write_text("---\napplyTo: '**/*.py'\ndescription: 'Python rules'\n---\n- No eval\n", encoding="utf-8")
    return InstructionFile(path=p, apply_to="**/*.py", description="Python rules", content="", rules=["No eval"])


def _make_violation(tmp_path: Path, severity: str = "error") -> Violation:
    f = tmp_path / "app.py"
    f.touch()
    return Violation(file=f, line_number=3, line_content="eval(x)", rule="No eval", severity=severity)


def test_markdown_reporter_creates_file(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=1)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    assert out.exists()


def test_markdown_report_has_header(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=1)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    assert "# Guideline Compliance Report" in content
    assert "## Summary" in content


def test_markdown_no_violations_message(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=2)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    assert "No violations found" in content


def test_markdown_shows_violations(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    v = _make_violation(tmp_path)
    result = RuleResult(instruction=instr, violations=[v], files_checked=1)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    assert "python.instructions.md" in content
    assert "eval(x)" in content
    assert "🔴" in content


def test_markdown_severity_emojis(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    violations = [
        _make_violation(tmp_path, severity="error"),
        _make_violation(tmp_path, severity="warning"),
        _make_violation(tmp_path, severity="info"),
    ]
    result = RuleResult(instruction=instr, violations=violations, files_checked=1)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    assert "🔴" in content
    assert "🟡" in content
    assert "🔵" in content


def test_markdown_skips_sections_with_no_violations(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    clean = RuleResult(instruction=instr, violations=[], files_checked=2)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[clean], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    # Sections with no violations should not appear in violations list
    assert "Violations by rule file" not in content


def test_markdown_creates_parent_dirs(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=0)
    out = tmp_path / "reports" / "nested" / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    assert out.exists()


def test_markdown_file_outside_root(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    external = tmp_path.parent / "external.py"
    external.write_text("eval(x)\n", encoding="utf-8")
    v = Violation(file=external, line_number=1, line_content="eval(x)", rule="No eval", severity="warning")
    result = RuleResult(instruction=instr, violations=[v], files_checked=1)
    out = tmp_path / "report.md"
    MarkdownReporter().write(results=[result], output_path=out, root=tmp_path)
    content = out.read_text(encoding="utf-8")
    assert "external.py" in content
