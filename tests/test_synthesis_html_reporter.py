"""Tests for the synthesis HTML reporter."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.linters import LinterResult, LinterViolation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.synthesis_html import SynthesisHtmlReporter


def _make_instruction(tmp_path: Path) -> InstructionFile:
    path = tmp_path / "test.instructions.md"
    path.write_text("# Test\n", encoding="utf-8")
    return InstructionFile(
        path=path,
        apply_to="**/*.py",
        description="Test guideline",
        content="# Test\n",
        rules=["No print() calls"],
    )


# ── Basic output ───────────────────────────────────────────────────────────────


def test_synthesis_empty_entries(tmp_path: Path) -> None:
    """Empty repo list → valid HTML with no errors."""
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=[], output_path=output)
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "Workspace Synthesis" in html


def test_synthesis_creates_parent_dirs(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "deep" / "synthesis.html"
    reporter = SynthesisHtmlReporter()
    reporter.write(workspace=tmp_path, repo_entries=[], output_path=output)
    assert output.exists()


# ── Skipped entries ────────────────────────────────────────────────────────────


def test_synthesis_skipped_entry(tmp_path: Path) -> None:
    entries = [
        {
            "name": "my-repo",
            "path": tmp_path / "my-repo",
            "skipped": True,
            "reason": "no .github directory",
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "my-repo" in html
    assert "SKIP" in html
    assert "no .github directory" in html


def test_synthesis_skipped_without_reason(tmp_path: Path) -> None:
    entries = [
        {
            "name": "other-repo",
            "path": tmp_path / "other-repo",
            "skipped": True,
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "other-repo" in html
    assert "unknown" in html


# ── Pass / fail entries ────────────────────────────────────────────────────────


def test_synthesis_pass_entry(tmp_path: Path) -> None:
    repo_dir = tmp_path / "clean-repo"
    repo_dir.mkdir()
    report_file = repo_dir / "guideline-report.html"
    entries = [
        {
            "name": "clean-repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 0,
            "warnings": 2,
            "results": [],
            "linter_results": [],
            "report_path": report_file,
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "PASS" in html
    assert "clean-repo" in html


def test_synthesis_fail_entry(tmp_path: Path) -> None:
    repo_dir = tmp_path / "bad-repo"
    repo_dir.mkdir()
    report_file = tmp_path / "guideline-report.html"
    entries = [
        {
            "name": "bad-repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 3,
            "warnings": 1,
            "results": [],
            "linter_results": [],
            "report_path": report_file,
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "FAIL" in html


def test_synthesis_error_counts_shown(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 5,
            "warnings": 3,
            "results": [],
            "linter_results": [],
            "report_path": tmp_path / "report.html",
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "out.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    # errors and warnings counts appear somewhere in stats / table
    assert "5" in html
    assert "3" in html


# ── Report path fallback ────────────────────────────────────────────────────────


def test_synthesis_report_path_relative(tmp_path: Path) -> None:
    """report_path inside output_path.parent → relative link."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    # report_path is relative to output dir
    report_file = output_dir / "repo" / "guideline-report.html"
    (output_dir / "repo").mkdir()
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 0,
            "warnings": 0,
            "results": [],
            "linter_results": [],
            "report_path": report_file,
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = output_dir / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "repo/guideline-report.html" in html


def test_synthesis_report_path_absolute_fallback(tmp_path: Path) -> None:
    """report_path outside output dir → absolute path used."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    report_file = other_dir / "guideline-report.html"
    output_dir = tmp_path / "reports"
    output_dir.mkdir()
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 0,
            "warnings": 0,
            "results": [],
            "linter_results": [],
            "report_path": report_file,
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = output_dir / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "guideline-report.html" in html


# ── Top rules / files aggregation ─────────────────────────────────────────────


def test_synthesis_with_rule_results(tmp_path: Path) -> None:
    """RuleResult violations should populate top-rules / top-files tables."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    instruction = _make_instruction(tmp_path)
    violation = Violation(
        file=repo_dir / "main.py",
        line_number=1,
        line_content='print("hi")',
        rule="No print() calls",
        severity="error",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 1,
            "warnings": 0,
            "results": [result],
            "linter_results": [],
            "report_path": tmp_path / "report.html",
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "main.py" in html


def test_synthesis_with_linter_results(tmp_path: Path) -> None:
    """LinterResult violations should populate top-rules / top-files tables."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    linter_violation = LinterViolation(
        file=repo_dir / "app.py",
        line=5,
        col=0,
        code="F401",
        message="unused import",
        severity="error",
        linter="ruff",
    )
    linter_result = LinterResult(linter="ruff", available=True, violations=[linter_violation])
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 1,
            "warnings": 0,
            "results": [],
            "linter_results": [linter_result],
            "report_path": tmp_path / "report.html",
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "F401" in html


def test_synthesis_violation_file_outside_repo(tmp_path: Path) -> None:
    """Violations outside repo path should use str(v.file) as fallback."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    external = tmp_path.parent / "external.py"
    instruction = _make_instruction(tmp_path)
    violation = Violation(
        file=external,
        line_number=1,
        line_content="x = 1",
        rule="No print() calls",
        severity="error",
    )
    result = RuleResult(instruction=instruction, violations=[violation], files_checked=1)
    entries = [
        {
            "name": "repo",
            "path": repo_dir,
            "skipped": False,
            "errors": 1,
            "warnings": 0,
            "results": [result],
            "linter_results": [],
            "report_path": tmp_path / "report.html",
        }
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    assert output.exists()


# ── Multi-repo stats ───────────────────────────────────────────────────────────


def test_synthesis_multiple_repos_totals(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_c = tmp_path / "repo-c"
    repo_a.mkdir()
    repo_b.mkdir()
    repo_c.mkdir()
    entries = [
        {
            "name": "repo-a",
            "path": repo_a,
            "skipped": False,
            "errors": 0,
            "warnings": 0,
            "results": [],
            "linter_results": [],
            "report_path": repo_a / "report.html",
        },
        {
            "name": "repo-b",
            "path": repo_b,
            "skipped": False,
            "errors": 2,
            "warnings": 1,
            "results": [],
            "linter_results": [],
            "report_path": repo_b / "report.html",
        },
        {
            "name": "repo-c",
            "path": repo_c,
            "skipped": True,
            "reason": "not a chrysa repo",
        },
    ]
    reporter = SynthesisHtmlReporter()
    output = tmp_path / "synthesis.html"
    reporter.write(workspace=tmp_path, repo_entries=entries, output_path=output)
    html = output.read_text(encoding="utf-8")
    assert "repo-a" in html
    assert "repo-b" in html
    assert "repo-c" in html
    assert "PASS" in html
    assert "FAIL" in html
    assert "SKIP" in html
