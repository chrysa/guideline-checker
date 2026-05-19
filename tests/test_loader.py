"""Tests for the instruction loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.loader import SourceType, load_all_sources, load_instructions

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


# ── SourceType ─────────────────────────────────────────────────────────────────


def test_instruction_file_has_source_type_copilot_instruction(instructions_dir: Path) -> None:
    """load_instructions returns files with COPILOT_INSTRUCTION source type."""
    result = load_instructions(instructions_dir)
    assert all(i.source_type == SourceType.COPILOT_INSTRUCTION for i in result)


# ── load_all_sources ───────────────────────────────────────────────────────────


def test_load_all_sources_finds_instructions(tmp_path: Path) -> None:
    """load_all_sources discovers .instructions.md files."""
    inst_dir = tmp_path / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "py.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'Python'\n---\n- No print calls allowed here\n",
        encoding="utf-8",
    )
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.COPILOT_INSTRUCTION for s in result)


def test_load_all_sources_finds_copilot_global(tmp_path: Path) -> None:
    """load_all_sources discovers .github/copilot-instructions.md."""
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("- Always use type annotations\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.COPILOT_GLOBAL for s in result)


def test_load_all_sources_finds_claude_md(tmp_path: Path) -> None:
    """load_all_sources discovers CLAUDE.md at project root."""
    (tmp_path / "CLAUDE.md").write_text("- Never hardcode secrets\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.CLAUDE for s in result)


def test_load_all_sources_finds_agents_md(tmp_path: Path) -> None:
    """load_all_sources discovers AGENTS.md at project root."""
    (tmp_path / "AGENTS.md").write_text("- Always respond in English\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.AGENTS for s in result)


def test_load_all_sources_finds_claude_agents_dir(tmp_path: Path) -> None:
    """load_all_sources discovers .claude/agents/*.md files."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "backend.md").write_text("- Must write tests for all endpoints\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.AGENTS for s in result)


def test_load_all_sources_empty_project(tmp_path: Path) -> None:
    """load_all_sources returns empty list when no instruction files are found."""
    result = load_all_sources(tmp_path)
    assert result == []


def test_load_all_sources_finds_dot_claude_claude_md(tmp_path: Path) -> None:
    """load_all_sources discovers .claude/CLAUDE.md in addition to CLAUDE.md."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "CLAUDE.md").write_text("- Always run lint before committing\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    assert any(s.source_type == SourceType.CLAUDE for s in result)


def test_source_type_claude_description(tmp_path: Path) -> None:
    """Claude sources have a description containing 'Claude'."""
    (tmp_path / "CLAUDE.md").write_text("- Always use async in FastAPI endpoints\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    claude = next(s for s in result if s.source_type == SourceType.CLAUDE)
    assert "Claude" in claude.description
    assert claude.apply_to == "**/*"


def test_source_type_copilot_global_description(tmp_path: Path) -> None:
    """Copilot global source has a description containing 'Copilot'."""
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text("- Use consistent naming conventions\n", encoding="utf-8")
    result = load_all_sources(tmp_path)
    copilot = next(s for s in result if s.source_type == SourceType.COPILOT_GLOBAL)
    assert "Copilot" in copilot.description


# ── _extract_rules — extended formats ─────────────────────────────────────────


def test_extract_rules_numbered_list(tmp_path: Path) -> None:
    """Numbered list items are extracted as rules."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "numbered.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'nums'\n---\n"
        "1. Always use type annotations in Python code\n"
        "2. Never use bare except clauses in production\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert len(result[0].rules) == 2


def test_extract_rules_table_row_with_keyword(tmp_path: Path) -> None:
    """Table rows containing constraint keywords are extracted as rules."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "table.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'tbl'\n---\n"
        "| Service | Must use health endpoint |\n"
        "| --- | --- |\n"
        "| API | must return 200 OK |\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert any("must" in r.lower() for r in result[0].rules)


def test_extract_rules_table_row_without_keyword_ignored(tmp_path: Path) -> None:
    """Table rows without constraint keywords are ignored."""
    inst_dir = tmp_path / "inst"
    inst_dir.mkdir()
    (inst_dir / "table.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'tbl'\n---\n| Name | Description |\n| foo | bar baz qux |\n",
        encoding="utf-8",
    )
    result = load_instructions(inst_dir)
    assert all("foo" not in r for r in result[0].rules)
