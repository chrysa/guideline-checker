"""Tests for the instruction loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.loader import load_instructions

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


def test_load_instructions_no_frontmatter(tmp_path: Path) -> None:
    """Should parse applyTo and description from raw content when no frontmatter block."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "plain.instructions.md").write_text(
        "description: Plain guidelines\napplyTo: **/*.md\n\n- Be concise\n- Use sentence case\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result) == 1
    assert result[0].apply_to == "**/*.md"
    assert result[0].description == "Plain guidelines"


def test_load_instructions_no_frontmatter_no_fields(tmp_path: Path) -> None:
    """Should fall back to defaults when no frontmatter and no fields in content."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "bare.instructions.md").write_text(
        "# Just rules\n\n- Rule one that matters\n- Rule two also\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result) == 1
    assert result[0].apply_to == "**/*"  # default
    assert result[0].description == "bare"  # stem fallback


def test_load_instructions_ignores_short_rules(tmp_path: Path) -> None:
    """Rules shorter than 10 characters should be filtered out."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "rules.instructions.md").write_text(
        "---\napplyTo: '**/*'\ndescription: 'Short'\n---\n- Ok\n- This rule is long enough\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result) == 1
    assert result[0].rules == ["This rule is long enough"]
