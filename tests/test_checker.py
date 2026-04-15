"""Tests for the checker engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import _matches_pattern, run_checks


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
