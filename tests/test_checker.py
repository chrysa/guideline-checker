"""Tests for the checker engine."""
from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import run_checks


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
