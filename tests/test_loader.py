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
    """Should return empty list when no instruction files exist."""
    empty_dir = tmp_path / "instructions"
    empty_dir.mkdir()
    result = load_instructions(empty_dir)
    assert result == []


def test_load_instructions_without_frontmatter(tmp_path: Path) -> None:
    """Should parse files without YAML frontmatter, using inline applyTo."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "plain.instructions.md").write_text(
        "applyTo: **/*.py\n\n- No print calls allowed\n- No bare except\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result) == 1
    assert result[0].apply_to == "**/*.py"
    assert len(result[0].rules) == 2


def test_load_instructions_no_apply_to_defaults_to_glob_all(tmp_path: Path) -> None:
    """Files without applyTo should default to **/*."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "minimal.instructions.md").write_text(
        "# Rules\n\n- No print calls\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert result[0].apply_to == "**/*"


def test_load_instructions_no_description_defaults_to_stem(tmp_path: Path) -> None:
    """Files without description field should use the file stem as description."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "my-rules.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\n---\n\n- No print calls\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert result[0].description == "my-rules.instructions"


def test_load_instructions_without_frontmatter_no_apply_to(tmp_path: Path) -> None:
    """Files without frontmatter and without applyTo line should default to **/*."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "nodesc.instructions.md").write_text(
        "- No bare except clauses allowed in production\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert result[0].apply_to == "**/*"


def test_load_instructions_without_frontmatter_with_description(tmp_path: Path) -> None:
    """Files without frontmatter but with inline description line should parse it."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "inline.instructions.md").write_text(
        "description: Inline description\napplyTo: **/*.ts\n\n- No console.log calls\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert result[0].description == "Inline description"


def test_extract_rules_short_lines_are_skipped(tmp_path: Path) -> None:
    """Rule lines shorter than 10 chars should be ignored."""
    inst_dir = tmp_path / "instructions"
    inst_dir.mkdir()
    (inst_dir / "short.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'short'\n---\n- short\n- This is a proper long rule sentence\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result[0].rules) == 1
    assert "proper" in result[0].rules[0]
    """Should return empty list for directory with no .instructions.md files."""
    result = load_instructions(tmp_path)
    assert result == []
