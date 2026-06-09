"""Tests for the structured YAML rule referential loader (guidelines/)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from guideline_checker.checker import run_checks
from guideline_checker.guidelines import (
    GuidelineError,
    _target_to_glob,
    load_yaml_guidelines,
)
from guideline_checker.loader import SourceType, load_all_sources

_CATEGORIES = """\
categories:
  - id: stack
    description: "Stack choices"
  - id: architecture
    description: "Architecture"
  - id: prompt-format
    description: "Prompt formatting"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def referential(tmp_path: Path) -> Path:
    """A minimal but complete guidelines/ tree across both dimensions."""
    root = tmp_path
    g = root / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "ai-models" / "claude.yml",
        "model_target: claude\nrules:\n"
        "  - id: claude-xml\n    category: prompt-format\n    severity: warning\n"
        '    rule: "Structure prompts with XML tags"\n    rationale: "Better adherence"\n',
    )
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        "  - id: py-pydantic\n    category: stack\n    severity: error\n"
        '    rule: "Pydantic v2 only"\n',
    )
    return root


def test_loads_both_dimensions(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    assert {i.source_type for i in instructions} == {SourceType.GUIDELINES_YAML}
    rules = {r for i in instructions for r in i.rules}
    assert rules == {"Structure prompts with XML tags", "Pydantic v2 only"}


def test_target_drives_apply_to(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    by_rule = {i.rules[0]: i for i in instructions}
    # Language target maps to its file glob; model target falls through to all files.
    assert by_rule["Pydantic v2 only"].apply_to == "**/*.py"
    assert by_rule["Structure prompts with XML tags"].apply_to == "**/*"


def test_explicit_severity_recorded(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    by_rule = {i.rules[0]: i for i in instructions}
    assert by_rule["Pydantic v2 only"].rule_severity["Pydantic v2 only"] == "error"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("python", "**/*.py"),
        ("typescript", "**/*.ts,**/*.tsx"),
        ("react", "**/*.tsx,**/*.jsx"),
        ("*", "**/*"),
        ("claude", "**/*"),  # unknown/model target -> all files
    ],
)
def test_target_to_glob(target: str, expected: str) -> None:
    assert _target_to_glob(target) == expected


def test_per_rule_target_override(tmp_path: Path) -> None:
    """A rule overriding language_target='*' inside python.yml applies to all files."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        '  - id: py-specific\n    category: stack\n    severity: warning\n    rule: "Python only rule"\n'
        '  - id: transverse\n    language_target: "*"\n    category: stack\n    severity: warning\n'
        '    rule: "Applies everywhere"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    globs = {i.rules[0]: i.apply_to for i in instructions}
    assert globs["Python only rule"] == "**/*.py"
    assert globs["Applies everywhere"] == "**/*"


def test_unknown_category_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: bad\n    category: nonexistent\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match="unknown category"):
        load_yaml_guidelines(tmp_path)


def test_invalid_severity_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: bad\n    category: stack\n    severity: critical\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match="invalid severity"):
        load_yaml_guidelines(tmp_path)


def test_missing_categories_file_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: x\n    category: stack\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match=r"categories\.yml"):
        load_yaml_guidelines(tmp_path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(g / "languages" / "python.yml", "language_target: python\nrules:\n  - id: x\n  bad: : :\n")
    with pytest.raises(GuidelineError, match="invalid YAML"):
        load_yaml_guidelines(tmp_path)


def test_duplicate_id_first_match_wins_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    # "_common.yml" is parsed first (sorted), so its rule wins the id tie.
    _write(
        g / "languages" / "_common.yml",
        'language_target: "*"\nrules:\n  - id: dup\n    category: stack\n    severity: error\n    rule: "First wins"\n',
    )
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        '  - id: dup\n    category: stack\n    severity: warning\n    rule: "Second skipped"\n',
    )
    with caplog.at_level(logging.WARNING):
        instructions = load_yaml_guidelines(tmp_path)
    rules = {r for i in instructions for r in i.rules}
    assert rules == {"First wins"}
    assert "duplicate rule id 'dup'" in caplog.text


def test_no_guidelines_dir_returns_empty(tmp_path: Path) -> None:
    assert load_yaml_guidelines(tmp_path) == []


def test_markdown_sources_have_empty_rule_severity(tmp_path: Path) -> None:
    """Regression: markdown-only projects keep rule_severity empty (no behaviour change)."""
    _write(tmp_path / "CLAUDE.md", "# Rules\n\n- No print() in production code\n")
    instructions = load_all_sources(tmp_path)
    assert instructions
    assert all(i.rule_severity == {} for i in instructions)


def test_severity_override_end_to_end(tmp_path: Path) -> None:
    """A detectable YAML rule reports the YAML severity, not the phrasing default.

    The phrasing "no print" defaults to *warning* in the engine; the YAML pins it
    to *error*, so the produced violation must carry severity == "error".
    """
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        "  - id: py-no-print\n    category: stack\n    severity: error\n"
        '    rule: "No print() in production code"\n',
    )
    _write(tmp_path / "src" / "app.py", "def main():\n    print('hello')\n")

    results = run_checks(tmp_path, all_sources=True)
    violations = [v for r in results for v in r.violations if "print" in v.rule.lower()]
    assert violations, "expected the detectable no-print rule to flag print("
    assert all(v.severity == "error" for v in violations)
