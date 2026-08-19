"""Integration tests: run guideline-checker against real fixture code."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guideline_checker.cli import main
from guideline_checker.core.detection import run_checks

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "real_project"
FIXTURE_INSTRUCTIONS = FIXTURE_ROOT / ".github" / "instructions"


@pytest.mark.skipif(
    not FIXTURE_ROOT.exists(),
    reason="Fixture project not found",
)
class TestRealProjectIntegration:
    """Run against the bundled fixture project which has known violations."""

    def test_finds_python_violations(self) -> None:
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        python_results = [r for r in results if "python" in r.instruction.path.name]
        assert len(python_results) > 0
        all_violations = [v for r in python_results for v in r.violations]
        # bad_code.py contains: eval, bare except, print, wildcard import, TODO, FIXME
        assert len(all_violations) > 0

    def test_detects_eval_in_bad_code(self) -> None:
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        all_violations = [v for r in results for v in r.violations]
        assert any("eval(" in v.line_content for v in all_violations)

    def test_detects_bare_except_in_bad_code(self) -> None:
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        all_violations = [v for r in results for v in r.violations]
        assert any("except:" in v.line_content for v in all_violations)

    def test_detects_wildcard_import_in_bad_code(self) -> None:
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        all_violations = [v for r in results for v in r.violations]
        assert any("import *" in v.line_content for v in all_violations)

    def test_good_code_has_fewer_violations(self) -> None:
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        # Find violations per file
        good_code_violations = [v for r in results for v in r.violations if "good_code" in str(v.file)]
        bad_code_violations = [v for r in results for v in r.violations if "bad_code" in str(v.file)]
        assert len(good_code_violations) < len(bad_code_violations)

    def test_inline_disable_suppresses_hardcoded_key(self) -> None:
        """bad_code.py has API_KEY line with # guideline: disable — should be skipped."""
        results = run_checks(root=FIXTURE_ROOT, instructions_dir=FIXTURE_INSTRUCTIONS, all_sources=False)
        all_violations = [v for r in results for v in r.violations]
        # The API_KEY line has guideline: disable, so it should NOT appear
        assert not any("API_KEY" in v.line_content for v in all_violations)

    def test_cli_exits_nonzero_on_real_violations(self, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        code = main(["check", "--root", str(FIXTURE_ROOT), "--output", str(out), "--no-multi-source"])
        assert code == 1  # violations found → exit 1

    def test_cli_produces_all_report_formats(self, tmp_path: Path) -> None:
        html_out = tmp_path / "report.html"
        json_out = tmp_path / "report.json"
        sarif_out = tmp_path / "report.sarif"
        md_out = tmp_path / "report.md"
        main(
            [
                "check",
                "--root",
                str(FIXTURE_ROOT),
                "--output",
                str(html_out),
                "--json",
                str(json_out),
                "--sarif",
                str(sarif_out),
                "--markdown",
                str(md_out),
                "--fail-on",
                "never",
                "--no-multi-source",
            ]
        )
        assert html_out.exists()
        assert json_out.exists()
        assert sarif_out.exists()
        assert md_out.exists()

    def test_json_report_has_expected_structure(self, tmp_path: Path) -> None:
        json_out = tmp_path / "report.json"
        main(
            [
                "check",
                "--root",
                str(FIXTURE_ROOT),
                "--json",
                str(json_out),
                "--fail-on",
                "never",
            ]
        )
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert data["summary"]["total_violations"] > 0
        assert len(data["rules"]) > 0

    def test_sarif_report_is_valid(self, tmp_path: Path) -> None:
        sarif_out = tmp_path / "report.sarif"
        main(
            [
                "check",
                "--root",
                str(FIXTURE_ROOT),
                "--sarif",
                str(sarif_out),
                "--fail-on",
                "never",
            ]
        )
        data = json.loads(sarif_out.read_text(encoding="utf-8"))
        assert data["version"] == "2.1.0"
        assert len(data["runs"][0]["results"]) > 0

    def test_markdown_report_content(self, tmp_path: Path) -> None:
        md_out = tmp_path / "report.md"
        main(
            [
                "check",
                "--root",
                str(FIXTURE_ROOT),
                "--markdown",
                str(md_out),
                "--fail-on",
                "never",
            ]
        )
        content = md_out.read_text(encoding="utf-8")
        assert "# Guideline Compliance Report" in content
        assert "🔴" in content or "🟡" in content
