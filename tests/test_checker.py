"""Tests for the checker engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import _build_checks, _matches_pattern, run_checks


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


# --- excludes ---


@pytest.fixture()
def project_with_excludable_files(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with files in multiple locations for exclude testing."""
    root = tmp_path / "project"
    root.mkdir()
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "python.instructions.md").write_text(
        '---\napplyTo: "**/*.py"\ndescription: "Python"\n---\n- No print() calls\n',
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text('print("hi")\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_main.py").write_text('print("test")\n', encoding="utf-8")
    (root / "vendor").mkdir()
    (root / "vendor" / "legacy.py").write_text('print("vendor")\n', encoding="utf-8")
    return root, inst_dir


def test_run_checks_without_excludes_finds_all(
    project_with_excludable_files: tuple[Path, Path],
) -> None:
    root, inst_dir = project_with_excludable_files
    results = run_checks(root=root, instructions_dir=inst_dir)
    assert len(results) == 1
    # 3 files with print() each
    assert len(results[0].violations) == 3


def test_run_checks_with_exclude_skips_tests(
    project_with_excludable_files: tuple[Path, Path],
) -> None:
    root, inst_dir = project_with_excludable_files
    results = run_checks(root=root, instructions_dir=inst_dir, excludes=["tests/**"])
    files = {str(v.file.relative_to(root)) for v in results[0].violations}
    assert "tests/test_main.py" not in files
    assert "src/main.py" in files
    assert "vendor/legacy.py" in files


def test_run_checks_with_multiple_excludes(
    project_with_excludable_files: tuple[Path, Path],
) -> None:
    root, inst_dir = project_with_excludable_files
    results = run_checks(root=root, instructions_dir=inst_dir, excludes=["tests/**", "vendor/**"])
    files = {str(v.file.relative_to(root)) for v in results[0].violations}
    assert files == {"src/main.py"}


def test_run_checks_exclude_single_file(
    project_with_excludable_files: tuple[Path, Path],
) -> None:
    root, inst_dir = project_with_excludable_files
    results = run_checks(root=root, instructions_dir=inst_dir, excludes=["vendor/legacy.py"])
    files = {str(v.file.relative_to(root)) for v in results[0].violations}
    assert "vendor/legacy.py" not in files


# --- _build_checks unit tests ---


class TestBuildChecks:
    """Unit tests for _build_checks (rule text → pattern mapping)."""

    def test_no_print_rule(self) -> None:
        assert ("print(", "warning") in _build_checks("no print calls")

    def test_no_console_warn(self) -> None:
        assert ("console.warn(", "warning") in _build_checks("no console.warn")

    def test_no_console_error(self) -> None:
        assert ("console.error(", "warning") in _build_checks("no console.error")

    def test_no_debugger(self) -> None:
        assert ("debugger", "error") in _build_checks("no debugger statements")

    def test_no_breakpoint(self) -> None:
        checks = _build_checks("no breakpoint() calls")
        assert ("breakpoint(", "error") in checks

    def test_no_todo(self) -> None:
        assert ("TODO", "info") in _build_checks("no todo comments")

    def test_no_fixme(self) -> None:
        assert ("FIXME", "warning") in _build_checks("no fixme markers")

    def test_no_hardcoded_password(self) -> None:
        checks = _build_checks("no hardcoded password")
        patterns = [p for p, _ in checks]
        assert "password =" in patterns
        assert "password=" in patterns

    def test_no_api_key(self) -> None:
        checks = _build_checks("no api key in code")
        patterns = [p for p, _ in checks]
        assert "api_key =" in patterns

    def test_unknown_rule_returns_empty(self) -> None:
        assert _build_checks("follow pep 8 conventions") == []
