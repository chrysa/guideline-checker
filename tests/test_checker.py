"""Tests for the checker engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from guideline_checker.checker import (
    IGNORE_FILES,
    PatternCheck,
    _collect_files,
    _expand_brace_pattern,
    _is_text_file,
    _line_matches,
    _matches_pattern,
    _narrow_apply_to,
    _resolve_max_file_size,
    _split_patterns,
    run_checks,
)
from guideline_checker.loader import InstructionFile


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
    results = run_checks(root=root, instructions_dir=inst_dir, all_sources=False)
    assert len(results) == 1
    violations = results[0].violations
    assert any("print" in v.line_content for v in violations)


def test_run_checks_empty_instructions(tmp_path: Path) -> None:
    """Should return empty results when no instructions exist."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    results = run_checks(root=tmp_path, instructions_dir=inst_dir, all_sources=False)
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
    results = run_checks(root=root, instructions_dir=inst_dir, all_sources=False)
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

    def test_no_exec_phrase_ignores_executable_word(self, tmp_path: Path) -> None:
        # "no executable runtime" (a container-runtime rule) must NOT be read
        # as "no exec()" and flag JS RegExp.exec() calls. Regression: the
        # canonical "exempt:config - no executable runtime" bullet flagged
        # every `.exec(` in the fleet.
        code = "const m = /^C(\\d+)/.exec(layer)\n"
        rule = "exempt:config - no executable runtime (config). Nothing to run."
        root, inst = _make_project(tmp_path, "app.ts", code, rule)
        results = run_checks(root=root, instructions_dir=inst)
        assert all(len(r.violations) == 0 for r in results)

    def test_no_eval_phrase_ignores_evaluation_word(self, tmp_path: Path) -> None:
        # "no evaluation ..." contains the substring "no eval" but is not a
        # "no eval()" rule; it must not flag eval(-shaped calls.
        code = "const score = model.evaluate(input)\n"
        rule = "no evaluation of untrusted strings at runtime"
        root, inst = _make_project(tmp_path, "app.ts", code, rule)
        results = run_checks(root=root, instructions_dir=inst)
        assert all(len(r.violations) == 0 for r in results)

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
        # Built at runtime so no single source literal reads as a real secret.
        fake = "aB3xK9mP2q" + "R7sT1vWc0dE"
        root, inst = _make_project(tmp_path, "app.py", f'password = "{fake}"\n', "No hardcoded password or secret")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("password" in v.line_content for r in results for v in r.violations)

    def test_detects_max_file_length_colon_syntax(self, tmp_path: Path) -> None:
        """Cover the 'max file length: N' regex variant (distinct from 'max N lines per file')."""
        root, inst = _make_project(tmp_path, "app.py", "\n".join(["x = 1"] * 600), "Max file length: 500")
        results = run_checks(root=root, instructions_dir=inst)
        assert len(results[0].violations) == 1

    def test_check_file_oserror_returns_empty(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """_check_file should return [] when a file cannot be read (OSError)."""

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
        mocker.patch.object(Path, "read_text", side_effect=OSError("permission denied"))
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
        # File that does NOT use from __future__ import annotations → should flag
        f.write_text("import os\n")
        violations = _check_file(f, instr)
        assert any("__future__" in v.line_content for v in violations)

        # File that has it → no violation
        f2 = tmp_path / "good.py"
        f2.write_text("from __future__ import annotations\nimport os\n")
        violations2 = _check_file(f2, instr)
        assert violations2 == []

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


# --- IGNORE_FILES / _collect_files unit tests ---


class TestCollectFiles:
    """Unit tests for _collect_files and IGNORE_FILES."""

    def test_ignore_files_contains_generated_report_names(self) -> None:
        """IGNORE_FILES must contain all guideline-checker output filenames."""
        assert "guideline-report.html" in IGNORE_FILES
        assert "guideline-report.json" in IGNORE_FILES
        assert "guideline-report.md" in IGNORE_FILES
        assert "guideline-synthesis.html" in IGNORE_FILES

    def test_collect_files_excludes_guideline_report_html(self, tmp_path: Path) -> None:
        """_collect_files should not return guideline-report.html."""
        (tmp_path / "guideline-report.html").write_text("<html>report</html>")
        (tmp_path / "app.py").write_text("x = 1\n")
        result = _collect_files(tmp_path)
        names = [p.name for p in result]
        assert "guideline-report.html" not in names
        assert "app.py" in names

    def test_collect_files_excludes_guideline_report_json(self, tmp_path: Path) -> None:
        """_collect_files should not return guideline-report.json."""
        (tmp_path / "guideline-report.json").write_text('{"rules": []}')
        (tmp_path / "module.ts").write_text("export const x = 1;\n")
        result = _collect_files(tmp_path)
        names = [p.name for p in result]
        assert "guideline-report.json" not in names
        assert "module.ts" in names

    def test_collect_files_excludes_git_worktree_copies(self, tmp_path: Path) -> None:
        """git worktree copies (e.g. .claude/worktrees/) are duplicates, not source."""
        wt = tmp_path / ".claude" / "worktrees" / "branch-x" / "src"
        wt.mkdir(parents=True)
        (wt / "dup.py").write_text("x = 1\n")
        (tmp_path / "real.py").write_text("y = 2\n")
        names = [p.name for p in _collect_files(tmp_path)]
        assert "real.py" in names
        assert "dup.py" not in names

    def test_collect_files_excludes_guideline_report_md(self, tmp_path: Path) -> None:
        """_collect_files should not return guideline-report.md."""
        (tmp_path / "guideline-report.md").write_text("# Report\n")
        (tmp_path / "README.md").write_text("# Readme\n")
        result = _collect_files(tmp_path)
        names = [p.name for p in result]
        assert "guideline-report.md" not in names
        assert "README.md" in names

    def test_collect_files_excludes_synthesis_html(self, tmp_path: Path) -> None:
        """_collect_files should not return guideline-synthesis.html."""
        (tmp_path / "guideline-synthesis.html").write_text("<html>synthesis</html>")
        (tmp_path / "index.html").write_text("<html></html>")
        result = _collect_files(tmp_path)
        names = [p.name for p in result]
        assert "guideline-synthesis.html" not in names
        assert "index.html" in names

    def test_collect_files_skips_node_modules(self, tmp_path: Path) -> None:
        """_collect_files should not descend into node_modules."""
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {};\n")
        (tmp_path / "src.py").write_text("x = 1\n")
        result = _collect_files(tmp_path)
        paths_str = [str(p) for p in result]
        assert not any("node_modules" in s for s in paths_str)
        assert any("src.py" in s for s in paths_str)

    def test_run_checks_ignores_generated_report_files(self, tmp_path: Path) -> None:
        """run_checks should not flag violations inside guideline-report.md."""
        root = tmp_path / "project"
        root.mkdir()
        inst_dir = root / ".github" / "instructions"
        inst_dir.mkdir(parents=True)
        (inst_dir / "no_print.instructions.md").write_text(
            '---\napplyTo: "**/*"\ndescription: "no print"\n---\n\n- No print() calls\n',
            encoding="utf-8",
        )
        # Clean source file
        (root / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
        # Generated report that contains the word "print" — should be ignored
        (root / "guideline-report.md").write_text("# Report\n\n- No print() calls\n", encoding="utf-8")
        results = run_checks(root=root, instructions_dir=inst_dir, all_sources=False)
        violations = [v for r in results for v in r.violations]
        flagged_files = {v.file_path for v in violations}
        assert not any("guideline-report" in str(f) for f in flagged_files)


# --- _split_patterns / _expand_brace_pattern tests ---


class TestSplitPatterns:
    """Unit tests for _split_patterns and _expand_brace_pattern."""

    def test_split_plain_comma(self) -> None:
        """Comma-separated patterns without braces are split correctly."""
        result = _split_patterns("**/*.py, **/*.pyi")
        assert result == ["**/*.py", "**/*.pyi"]

    def test_split_preserves_brace_commas(self) -> None:
        """Commas inside braces must NOT split the pattern."""
        result = _split_patterns("{api,admin}/**/*.py, **/*.md")
        assert "{api,admin}/**/*.py" in result
        assert "**/*.md" in result
        assert len(result) == 2

    def test_split_nested_braces(self) -> None:
        """Opening brace increments depth; inner commas stay grouped."""
        result = _split_patterns("{a,{b,c}}/**")
        assert len(result) == 1
        assert "{a,{b,c}}/**" in result

    def test_split_trailing_brace_decrements_depth(self) -> None:
        """Closing brace decrements depth; comma after it splits as expected."""
        result = _split_patterns("{a,b}/**/*.py,**/*.ts")
        assert len(result) == 2

    def test_expand_brace_no_brace(self) -> None:
        """Pattern without braces is returned as-is (single-element list)."""
        result = _expand_brace_pattern("**/*.py")
        assert result == ["**/*.py"]

    def test_expand_brace_simple(self) -> None:
        """Single brace group is expanded into alternatives."""
        result = _expand_brace_pattern("{api,admin}/**/*.py")
        assert "api/**/*.py" in result
        assert "admin/**/*.py" in result
        assert len(result) == 2

    def test_expand_brace_multiple_groups(self) -> None:
        """Multiple brace groups are fully expanded (all combinations)."""
        result = _expand_brace_pattern("{a,b}/{x,y}.py")
        assert len(result) == 4
        assert "a/x.py" in result
        assert "b/y.py" in result


# --- _narrow_apply_to tests ---


class TestNarrowApplyTo:
    """Unit tests for _narrow_apply_to."""

    def _make_instruction(self, name: str, apply_to: str = "**/*") -> InstructionFile:
        from pathlib import Path

        return InstructionFile(
            path=Path(f".github/instructions/{name}.md"),
            apply_to=apply_to,
            description=name,
            content="",
            rules=[],
        )

    def test_explicit_apply_to_not_narrowed(self) -> None:
        """Instructions with an explicit apply_to are not modified."""
        instr = self._make_instruction("anything", apply_to="**/*.ts")
        result = _narrow_apply_to(instr)
        assert result.apply_to == "**/*.ts"

    def test_test_keyword_narrows_to_test_pattern(self) -> None:
        """Instructions with 'test' in filename are narrowed to test file patterns."""
        instr = self._make_instruction("test_performance")
        result = _narrow_apply_to(instr)
        assert "tests" in result.apply_to

    def test_makefile_keyword_narrows(self) -> None:
        """Instructions with 'makefile' in filename are narrowed to Makefile patterns."""
        instr = self._make_instruction("makefiles_guidelines")
        result = _narrow_apply_to(instr)
        assert "Makefile" in result.apply_to

    def test_no_keyword_unchanged(self) -> None:
        """Instructions without a recognised keyword keep **/* apply_to."""
        instr = self._make_instruction("generic_guidelines")
        result = _narrow_apply_to(instr)
        assert result.apply_to == "**/*"


# --- length-rule tests ---


class TestLengthRules:
    """Tests for function-length and file-length rules."""

    def test_function_length_rule_flags_long_function(self, tmp_path: Path) -> None:
        """A Python file with a function exceeding the limit should produce a warning."""
        from guideline_checker.loader import InstructionFile

        long_func = "\n".join(["def long_func():"] + ["    x = 1"] * 15)
        f = tmp_path / "long.py"
        f.write_text(long_func)
        from guideline_checker.checker import _check_file

        instr = InstructionFile(
            path=tmp_path / "rules.md",
            apply_to="**/*.py",
            description="length check",
            content="- max function length: 10",
            rules=["max function length: 10"],
        )
        violations = _check_file(f, instr)
        assert any("long_func" in v.line_content for v in violations)

    def test_function_length_two_functions_second_too_long(self, tmp_path: Path) -> None:
        """Multiple functions: only the one exceeding the limit is flagged."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        code = "def short():\n    return 1\n\n" + "def long_fn():\n" + "    x = 1\n" * 12
        f = tmp_path / "multi.py"
        f.write_text(code)
        instr = InstructionFile(
            path=tmp_path / "rules.md",
            apply_to="**/*.py",
            description="length",
            content="- max function length: 5",
            rules=["max function length: 5"],
        )
        violations = _check_file(f, instr)
        assert any("long_fn" in v.line_content for v in violations)

    def test_file_length_rule_flags_long_file(self, tmp_path: Path) -> None:
        """A file exceeding the max line count should produce a warning."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        f = tmp_path / "big.py"
        f.write_text("\n".join([f"x_{i} = {i}" for i in range(200)]))
        instr = InstructionFile(
            path=tmp_path / "rules.md",
            apply_to="**/*.py",
            description="file length",
            content="- max file length: 50",
            rules=["max file length: 50"],
        )
        violations = _check_file(f, instr)
        assert len(violations) >= 1
        assert "lines" in violations[0].line_content


# --- credential (entropy scan) / _docker_checks tests ---


class TestSecurityPatternChecks:
    """Tests for Docker and credential pattern checks."""

    def test_docker_no_root_user(self, tmp_path: Path) -> None:
        """Docker check: 'run as non-root' flags USER root."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        f = tmp_path / "Dockerfile"
        f.write_text("FROM ubuntu\nUSER root\nRUN apt-get install -y curl\n")
        instr = InstructionFile(
            path=tmp_path / "docker.md",
            apply_to="**/Dockerfile*",
            description="docker",
            content="- Run as non-root user in production",
            rules=["Run as non-root user in production"],
        )
        violations = _check_file(f, instr)
        assert any("USER root" in v.line_content for v in violations)

    def test_docker_no_latest_tag(self, tmp_path: Path) -> None:
        """Docker check: 'no latest tag' flags :latest usage."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        f = tmp_path / "Dockerfile"
        f.write_text("FROM ubuntu:latest\nRUN echo hello\n")
        instr = InstructionFile(
            path=tmp_path / "docker.md",
            apply_to="**/Dockerfile*",
            description="docker",
            content="- No latest tag in FROM instructions",
            rules=["No latest tag in FROM instructions"],
        )
        violations = _check_file(f, instr)
        assert any(":latest" in v.line_content for v in violations)

    def test_credential_hardcoded_secret_literal(self, tmp_path: Path) -> None:
        """Credential check: a high-entropy hardcoded secret literal is an error."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        f = tmp_path / "config.py"
        fake = "aB3xK9mP2q" + "R7sT1vWc0dE"  # runtime-built; no source literal reads as a secret
        f.write_text(f'DB_PASSWORD = "{fake}"\n')
        instr = InstructionFile(
            path=tmp_path / "secrets.md",
            apply_to="**/*.py",
            description="no hardcoded secrets",
            content="- No hardcoded password or secret in code, all via env vars",
            rules=["No hardcoded password or secret in code, all via env vars"],
        )
        violations = _check_file(f, instr, root=tmp_path)
        assert any(v.severity == "error" and "PASSWORD" in v.line_content for v in violations)

    def test_credential_ignores_value_read_from_a_call(self, tmp_path: Path) -> None:
        """Credential check: reading a token from a call is not a hardcoded secret."""
        from guideline_checker.checker import _check_file
        from guideline_checker.loader import InstructionFile

        f = tmp_path / "auth.py"
        f.write_text("token = response.json()['access_token']\n")
        instr = InstructionFile(
            path=tmp_path / "secrets.md",
            apply_to="**/*.py",
            description="no hardcoded secrets",
            content="- No hardcoded password or secret in code, all via env vars",
            rules=["No hardcoded password or secret in code, all via env vars"],
        )
        assert _check_file(f, instr, root=tmp_path) == []


class TestDjangoChecks:
    """Django / DRF anti-pattern detection (settings hardening + ORM safety)."""

    def test_detects_debug_true(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "settings.py", "DEBUG = True\n", "No DEBUG = True in committed settings")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("debug = true" in v.line_content.lower() for r in results for v in r.violations)

    def test_detects_wildcard_allowed_hosts(self, tmp_path: Path) -> None:
        root, inst = _make_project(tmp_path, "settings.py", 'ALLOWED_HOSTS = ["*"]\n', "No wildcard ALLOWED_HOSTS")
        results = run_checks(root=root, instructions_dir=inst)
        assert any("allowed_hosts" in v.line_content.lower() for r in results for v in r.violations)

    def test_detects_cors_allow_all(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path, "settings.py", "CORS_ALLOW_ALL_ORIGINS = True\n", "No CORS_ALLOW_ALL_ORIGINS = True"
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert any("cors_allow_all_origins" in v.line_content.lower() for r in results for v in r.violations)

    def test_detects_raw_sql(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path, "views.py", "qs = User.objects.raw('SELECT * FROM users')\n", "No raw SQL (no .raw()"
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert any(".raw(" in v.line_content for r in results for v in r.violations)

    def test_detects_hardcoded_secret_key(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path,
            "settings.py",
            'SECRET_KEY = "hardcoded-key"\n',
            "No hardcoded SECRET_KEY (load secret_key from env)",
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert any("secret_key" in v.line_content.lower() for r in results for v in r.violations)

    def test_no_false_positive_clean_settings(self, tmp_path: Path) -> None:
        root, inst = _make_project(
            tmp_path,
            "settings.py",
            'import os\nDEBUG = os.environ.get("DEBUG") == "1"\n',
            "No DEBUG = True in committed settings",
        )
        results = run_checks(root=root, instructions_dir=inst)
        assert all(len(r.violations) == 0 for r in results)


# --- L1.5 configurable max file size ---


class TestMaxFileSize:
    """Unit tests for the configurable scan size limit (_resolve_max_file_size)."""

    _DEFAULT = 200 * 1024

    def test_default_when_no_override_or_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No override and no env var falls back to the 200 KiB default."""
        monkeypatch.delenv("GUIDELINE_MAX_FILE_SIZE", raising=False)
        assert _resolve_max_file_size() == self._DEFAULT

    def test_explicit_override_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit override takes precedence over the env var."""
        monkeypatch.setenv("GUIDELINE_MAX_FILE_SIZE", "9999")
        assert _resolve_max_file_size(500_000) == 500_000

    def test_env_var_used_when_no_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The env var is honoured when no explicit override is given."""
        monkeypatch.setenv("GUIDELINE_MAX_FILE_SIZE", "500000")
        assert _resolve_max_file_size() == 500_000

    @pytest.mark.parametrize("bad", ["not-a-number", "0", "-5", ""])
    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
        """A non-positive or non-numeric env value falls back to the default."""
        monkeypatch.setenv("GUIDELINE_MAX_FILE_SIZE", bad)
        assert _resolve_max_file_size() == self._DEFAULT

    @pytest.mark.parametrize("bad_override", [0, -1, -200_000])
    def test_non_positive_override_falls_back(self, monkeypatch: pytest.MonkeyPatch, bad_override: int) -> None:
        """A non-positive CLI override is rejected (would disable the size filter)."""
        monkeypatch.delenv("GUIDELINE_MAX_FILE_SIZE", raising=False)
        assert _resolve_max_file_size(bad_override) == self._DEFAULT

    def test_is_text_file_respects_limit(self, tmp_path: Path) -> None:
        """A file above the limit is rejected; below the limit it is accepted."""
        big = tmp_path / "big.py"
        big.write_text("x = 1\n" * 40_000)  # ~240 KB, above the default
        assert _is_text_file(big, self._DEFAULT) is False
        assert _is_text_file(big, 500_000) is True

    def test_collect_files_skips_oversized_file_by_default(self, tmp_path: Path) -> None:
        """_collect_files drops files larger than the default limit."""
        (tmp_path / "big.py").write_text("x = 1\n" * 40_000)
        (tmp_path / "small.py").write_text("x = 1\n")
        names = [p.name for p in _collect_files(tmp_path)]
        assert "big.py" not in names
        assert "small.py" in names

    def test_collect_files_includes_oversized_when_limit_raised(self, tmp_path: Path) -> None:
        """A raised limit lets _collect_files return a previously-skipped large file."""
        (tmp_path / "big.py").write_text("x = 1\n" * 40_000)
        names = [p.name for p in _collect_files(tmp_path, max_file_size=500_000)]
        assert "big.py" in names

    def test_collect_files_honours_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_collect_files resolves the limit from the env var when no arg is passed."""
        (tmp_path / "big.py").write_text("x = 1\n" * 40_000)
        monkeypatch.setenv("GUIDELINE_MAX_FILE_SIZE", "500000")
        names = [p.name for p in _collect_files(tmp_path)]
        assert "big.py" in names
