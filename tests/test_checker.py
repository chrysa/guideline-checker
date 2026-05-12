"""Tests for the checker engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import PatternCheck, _line_matches, _matches_pattern, run_checks


@pytest.fixture()
def project_with_violations(tmp_path: Path) -> tuple[Path, Path]:
    """Create a sample project with known violations."""
    root = tmp_path / "project"
    root.mkdir()

    # Create instruction file
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "python_guidelines.instructions.md").write_text(
        """---
applyTo: "**/*.py"
description: "Python guidelines"
---

- No print() calls in production code
- No bare except clauses
""",
        encoding="utf-8",
    )

    # Create Python file with violations
    src_dir = root / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text(
        """def my_function():
    print("This should be removed")
    try:
        pass
    except:
        pass
""",
        encoding="utf-8",
    )

    return root, inst_dir


def test_run_checks_finds_violations(project_with_violations: tuple[Path, Path]) -> None:
    """Should find violations in files matching applyTo pattern."""
    root, inst_dir = project_with_violations
    results = run_checks(root=root, instructions_dir=inst_dir)
    assert len(results) == 1
    violations = results[0].violations
    assert any("print" in v.line_content for v in violations)


def test_run_checks_empty_instructions(tmp_path: Path) -> None:
    """Should return empty results when no instructions exist."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    results = run_checks(root=tmp_path, instructions_dir=inst_dir)
    assert results == []


def test_run_checks_no_violations(tmp_path: Path) -> None:
    """Should find no violations in clean code."""
    root = tmp_path / "clean"
    root.mkdir()
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "python_guidelines.instructions.md").write_text(
        """---
applyTo: "**/*.py"
description: "Python guidelines"
---

- No print() calls
""",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        """def my_function() -> str:
    return "clean code"
""",
        encoding="utf-8",
    )
    results = run_checks(root=root, instructions_dir=inst_dir)
    assert all(len(r.violations) == 0 for r in results)


# --- _matches_pattern unit tests ---


class TestMatchesPattern:
    """Unit tests for _matches_pattern glob matching."""

    def test_double_star_matches_nested_file(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "app.py"
        f.parent.mkdir()
        f.touch()
        assert _matches_pattern(f, tmp_path, "**/*.py") is True

    def test_double_star_matches_root_level_file(self, tmp_path: Path) -> None:
        f = tmp_path / "app.py"
        f.touch()
        assert _matches_pattern(f, tmp_path, "**/*.py") is True

    def test_double_star_matches_deeply_nested(self, tmp_path: Path) -> None:
        f = tmp_path / "a" / "b" / "c" / "deep.py"
        f.parent.mkdir(parents=True)
        f.touch()
        assert _matches_pattern(f, tmp_path, "**/*.py") is True

    def test_no_match_wrong_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "app.js"
        f.touch()
        assert _matches_pattern(f, tmp_path, "**/*.py") is False

    def test_comma_separated_patterns(self, tmp_path: Path) -> None:
        py = tmp_path / "app.py"
        js = tmp_path / "app.js"
        txt = tmp_path / "readme.txt"
        py.touch()
        js.touch()
        txt.touch()
        assert _matches_pattern(py, tmp_path, "**/*.py, **/*.js") is True
        assert _matches_pattern(js, tmp_path, "**/*.py, **/*.js") is True
        assert _matches_pattern(txt, tmp_path, "**/*.py, **/*.js") is False

    def test_simple_filename_pattern(self, tmp_path: Path) -> None:
        f = tmp_path / "Makefile"
        f.touch()
        assert _matches_pattern(f, tmp_path, "Makefile") is True

    def test_directory_specific_pattern(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "main.py"
        f.parent.mkdir()
        f.touch()
        assert _matches_pattern(f, tmp_path, "src/*.py") is True

    def test_file_outside_root_returns_false(self, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        f = other / "app.py"
        f.touch()
        root = tmp_path / "project"
        root.mkdir()
        assert _matches_pattern(f, root, "**/*.py") is False

    def test_wildcard_all_pattern(self, tmp_path: Path) -> None:
        f = tmp_path / "anything.txt"
        f.touch()
        assert _matches_pattern(f, tmp_path, "**/*") is True


# --- _line_matches unit tests ---


class TestLineMatches:
    """Unit tests for the updated _line_matches function."""

    def test_skips_comment_line_by_default(self) -> None:
        assert _line_matches("    # print(debug)", "print(") is False

    def test_matches_code_line(self) -> None:
        assert _line_matches('    print("hello")', "print(") is True

    def test_match_in_comments_enabled(self) -> None:
        assert _line_matches("    # TODO: fix this", "TODO", match_in_comments=True) is True

    def test_match_in_comments_disabled(self) -> None:
        assert _line_matches("    # TODO: fix this", "TODO", match_in_comments=False) is False

    def test_case_insensitive_match(self) -> None:
        assert _line_matches("    except:", "EXCEPT:") is True

    def test_skips_js_comment(self) -> None:
        assert _line_matches("    // console.log(x)", "console.log(") is False


# --- PatternCheck rule-engine v0.2 tests ---


def _make_project(tmp_path: Path, filename: str, content: str, rule: str) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".py"
    (inst_dir / "rules.instructions.md").write_text(
        f"---\napplyTo: '**/*{ext}'\ndescription: 'test'\n---\n- {rule}\n",
        encoding="utf-8",
    )
    (root / filename).write_text(content, encoding="utf-8")
    return root, inst_dir


class TestRuleEngineV02:
    """Tests for the extended pattern-matching engine (v0.2)."""

    def test_detects_eval(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'result = eval("1+1")\n', "No eval() calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("eval(" in v.line_content for r in results for v in r.violations)

    def test_detects_exec(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'exec("code")\n', "No exec() calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("exec(" in v.line_content for r in results for v in r.violations)

    def test_detects_wildcard_import(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "from os import *\n", "No wildcard imports")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("import *" in v.line_content for r in results for v in r.violations)

    def test_detects_todo_in_comment(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "x = 1  # TODO: fix this\n", "No TODO comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("TODO" in v.line_content for r in results for v in r.violations)

    def test_detects_fixme_in_comment(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "# FIXME: broken\n", "No FIXME comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("FIXME" in v.line_content for r in results for v in r.violations)

    def test_detects_debugger_statement(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.js", "debugger;\n", "No debugger statements")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("debugger" in v.line_content for r in results for v in r.violations)

    def test_no_false_positive_clean_code(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "def clean() -> str:\n    return 'ok'\n", "No eval() calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert all(len(r.violations) == 0 for r in results)

    def test_pattern_check_namedtuple(self) -> None:
        pc = PatternCheck("print(", "warning")
        assert pc.pattern == "print("
        assert pc.severity == "warning"
        assert pc.match_in_comments is False

    def test_pattern_check_with_match_in_comments(self) -> None:
        pc = PatternCheck("TODO", "warning", match_in_comments=True)
        assert pc.match_in_comments is True

    def test_detects_pprint(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "pprint(data)\n", "No pprint() calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("pprint(" in v.line_content for r in results for v in r.violations)

    def test_detects_console_debug(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.js", "console.debug(x);\n", "No console.debug calls")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("console.debug(" in v.line_content for r in results for v in r.violations)

    def test_detects_hack_in_comment(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "x = 1  # HACK: workaround\n", "No HACK comments")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("HACK" in v.line_content for r in results for v in r.violations)

    def test_detects_assert_outside_test(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", "assert x > 0\n", "No assert statements in production")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("assert " in v.line_content for r in results for v in r.violations)

    def test_detects_hardcoded_password(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "app.py", 'password = "supersecret"\n', "No hardcoded password or secret")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("password" in v.line_content for r in results for v in r.violations)

    def test_detects_max_file_length_colon_syntax(self, tmp_path: Path) -> None:
        """Cover the 'max file length: N' regex variant (distinct from 'max N lines per file')."""
        root, inst = _make_project(tmp_path, "app.py", "\n".join(["x = 1"] * 600), "Max file length: 500")
        results = run_checks(root=root, instructions_dir=inst)
        assert len(results[0].violations) == 1

    def test_check_file_oserror_returns_empty(self, tmp_path: Path) -> None:
        """_check_file should return [] when a file cannot be read (OSError)."""
        from unittest.mock import patch

        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "rules.instructions.md",
            apply_to="**/*.py",
            description="test",
            content="- No print",
            rules=["No print() calls"],
        )
        fake_file = tmp_path / "unreadable.py"
        fake_file.touch()
        # Patch at class level — instance-level patching is read-only in Python 3.14+
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            violations = _check_file(fake_file, instr)
        assert violations == []

    def test_debug_output_console_log_in_python_context(self, tmp_path: Path) -> None:
        """_debug_output_checks: no console.log rule detected in non-TS file."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "python.instructions.md",
            apply_to="**/*.py",
            description="no console.log",
            content="- No console.log calls",
            rules=["No console.log calls"],
        )
        f = tmp_path / "bad.py"
        f.write_text("x = console.log('hi')\n")
        violations = _check_file(f, instr)
        assert any("console.log" in v.line_content for v in violations)

    def test_import_relative_import_check(self, tmp_path: Path) -> None:
        """_import_checks: relative import detection."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "python.instructions.md",
            apply_to="**/*.py",
            description="no relative import",
            content="- No relative import",
            rules=["No relative import"],
        )
        f = tmp_path / "mod.py"
        f.write_text("from . import utils\nfrom .. import base\n")
        violations = _check_file(f, instr)
        contents = [v.line_content for v in violations]
        assert any("from . import" in c for c in contents)
        assert any("from .. import" in c for c in contents)

    def test_annotation_check_future_annotations(self, tmp_path: Path) -> None:
        """_annotation_checks: __future__ import annotations rule."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "python.instructions.md",
            apply_to="**/*.py",
            description="future annotations",
            content="- Always use from __future__ import annotations",
            rules=["Always use from __future__ import annotations"],
        )
        f = tmp_path / "mod.py"
        # File that does NOT use from __future__ import annotations (no __future__)
        f.write_text("import os\n")
        violations = _check_file(f, instr)
        # Should NOT flag (pattern not found = no violation)
        assert violations == []

        # File that uses it — pattern found should not be a violation (info, triggers)
        f2 = tmp_path / "good.py"
        f2.write_text("from __future__ import annotations\nimport os\n")
        violations2 = _check_file(f2, instr)
        assert any("__future__" in v.line_content for v in violations2)

    def test_typescript_console_debug_check(self, tmp_path: Path) -> None:
        """_typescript_checks: no console.debug in TS files."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "typescript.instructions.md",
            apply_to="**/*.ts",
            description="no console.debug",
            content="- No console.debug calls",
            rules=["No console.debug calls"],
        )
        f = tmp_path / "util.ts"
        f.write_text("console.debug('trace');\n")
        violations = _check_file(f, instr)
        assert any("console.debug" in v.line_content for v in violations)

    def test_python_strict_no_pass_in_except(self, tmp_path: Path) -> None:
        """_python_strict_checks: no pass in except / silent exception."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        instr = InstructionFile(
            path=tmp_path / "python.instructions.md",
            apply_to="**/*.py",
            description="no silent exception",
            content="- No pass in except / no silent exception",
            rules=["No pass in except"],
        )
        f = tmp_path / "bad.py"
        f.write_text("try:\n    pass\nexcept:\n    pass\n")
        violations = _check_file(f, instr)
        assert any("except:" in v.line_content for v in violations)
