"""Tests for the SARIF 2.1.0 reporter."""

from __future__ import annotations

import json
from pathlib import Path

from pytest_mock import MockerFixture

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.loader import InstructionFile
from guideline_checker.reporters.sarif import SarifReporter, _sanitize_rule_id


def _make_instruction(tmp_path: Path, name: str = "python.instructions") -> InstructionFile:
    p = tmp_path / f"{name}.md"
    p.write_text("---\napplyTo: '**/*.py'\ndescription: 'Python rules'\n---\n- No eval\n", encoding="utf-8")
    return InstructionFile(path=p, apply_to="**/*.py", description="Python rules", content="", rules=["No eval"])


def _make_violation(tmp_path: Path, severity: str = "error") -> Violation:
    f = tmp_path / "app.py"
    f.write_text("eval(x)\n", encoding="utf-8")
    return Violation(file=f, line_number=1, line_content="eval(x)", rule="No eval", severity=severity)


def test_sarif_reporter_creates_file(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=1)
    reporter = SarifReporter()
    out = tmp_path / "report.sarif"
    reporter.write(results=[result], output_path=out, root=tmp_path)
    assert out.exists()


def test_sarif_report_is_valid_json(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=1)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"
    assert "$schema" in data
    assert "runs" in data


def test_sarif_report_has_tool_info(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=1)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    tool = data["runs"][0]["tool"]["driver"]
    assert tool["name"] == "guideline-checker"
    assert "rules" in tool


def test_sarif_report_has_violations(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    v = _make_violation(tmp_path)
    result = RuleResult(instruction=instr, violations=[v], files_checked=1)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    results_list = data["runs"][0]["results"]
    assert len(results_list) == 1
    assert results_list[0]["level"] == "error"
    assert results_list[0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_sarif_severity_mapping(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    for severity, expected_level in [("error", "error"), ("warning", "warning"), ("info", "note")]:
        v = _make_violation(tmp_path, severity=severity)
        result = RuleResult(instruction=instr, violations=[v], files_checked=1)
        out = tmp_path / f"report_{severity}.sarif"
        SarifReporter().write(results=[result], output_path=out, root=tmp_path)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["runs"][0]["results"][0]["level"] == expected_level


def test_sarif_creates_parent_dirs(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=0)
    out = tmp_path / "nested" / "deep" / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    assert out.exists()


def test_sarif_empty_results(tmp_path: Path) -> None:
    instr = _make_instruction(tmp_path)
    result = RuleResult(instruction=instr, files_checked=0)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["runs"][0]["results"] == []


def test_sarif_file_outside_root(tmp_path: Path) -> None:
    """Violation file outside root should use absolute path."""
    instr = _make_instruction(tmp_path)
    external = tmp_path.parent / "external.py"
    external.write_text("eval(x)\n", encoding="utf-8")
    v = Violation(file=external, line_number=1, line_content="eval(x)", rule="No eval", severity="error")
    result = RuleResult(instruction=instr, violations=[v], files_checked=1)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[result], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]


def test_sarif_deduplicates_rules(tmp_path: Path) -> None:
    """Multiple results from same instruction file should produce one SARIF rule."""
    instr = _make_instruction(tmp_path)
    r1 = RuleResult(instruction=instr, files_checked=1)
    r2 = RuleResult(instruction=instr, files_checked=1)
    out = tmp_path / "report.sarif"
    SarifReporter().write(results=[r1, r2], output_path=out, root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["runs"][0]["tool"]["driver"]["rules"]) == 1


def test_sanitize_rule_id_replaces_spaces() -> None:
    assert _sanitize_rule_id("python guidelines") == "python-guidelines"


def test_sanitize_rule_id_allows_dots_slashes() -> None:
    result = _sanitize_rule_id("python.instructions/v2")
    assert "." in result
    assert "/" in result


def test_sarif_get_version_fallback_on_import_error(tmp_path: Path, mocker: MockerFixture) -> None:
    """_get_version should return '0.0.0' if __version__ cannot be imported."""
    import sys

    reporter = SarifReporter()
    # Simulate ImportError when importing guideline_checker.__version__
    mocker.patch.dict(sys.modules, {"guideline_checker": None})  # type: ignore[dict-item]
    version = reporter._get_version()
    assert version == "0.0.0"
