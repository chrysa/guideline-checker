"""Tests for the instruction loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.loader import InstructionFile, load_instructions


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def instructions_dir(tmp_path: Path) -> Path:
    """Create a temporary instructions directory with sample files."""
    inst_dir = tmp_path / ".github" / "instructions"
    inst_dir.mkdir(parents=True)

    (inst_dir / "python_guidelines.instructions.md").write_text(
        """---
applyTo: "**/*.py"
description: "Python development guidelines"
---

## Python Guidelines

- No `print()` calls in production code
- All public functions must have type annotations
- Use `from __future__ import annotations` in every Python file
""",
        encoding="utf-8",
    )

    (inst_dir / "typescript.instructions.md").write_text(
        """---
applyTo: "**/*.ts,**/*.tsx"
description: "TypeScript guidelines"
---

## TypeScript Guidelines

- No `console.log()` in production code
- All props must have TypeScript types
""",
        encoding="utf-8",
    )

    return inst_dir


def test_load_instructions_count(instructions_dir: Path) -> None:
    """Should load all .instructions.md files from directory."""
    result = load_instructions(instructions_dir)
    assert len(result) == 2


def test_load_instructions_apply_to(instructions_dir: Path) -> None:
    """Should correctly parse the applyTo field."""
    result = load_instructions(instructions_dir)
    by_name = {i.path.name: i for i in result}
    assert by_name["python_guidelines.instructions.md"].apply_to == "**/*.py"
    assert by_name["typescript.instructions.md"].apply_to == "**/*.ts,**/*.tsx"


def test_load_instructions_description(instructions_dir: Path) -> None:
    """Should correctly parse the description field."""
    result = load_instructions(instructions_dir)
    by_name = {i.path.name: i for i in result}
    assert "Python" in by_name["python_guidelines.instructions.md"].description


def test_load_instructions_rules(instructions_dir: Path) -> None:
    """Should extract rules from instruction file content."""
    result = load_instructions(instructions_dir)
    python_instr = next(i for i in result if "python" in i.path.name)
    assert len(python_instr.rules) > 0


def test_load_instructions_empty_dir(tmp_path: Path) -> None:
    """Should return empty list for directory with no .instructions.md files."""
    result = load_instructions(tmp_path)
    assert result == []
