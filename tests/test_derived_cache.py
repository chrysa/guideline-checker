"""Tests for the interpret-once derived cache (ADR D-0016)."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.guidelines import load_yaml_guidelines
from guideline_checker.loader import RuleDetector
from guideline_checker.workshop.interpret import DerivedRule
from guideline_checker.workshop.persist import write_derived_ruleset

_CATEGORIES = "categories:\n  - id: correctness\n    description: c\n  - id: security\n    description: s\n"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "guidelines").mkdir()
    (tmp_path / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    return tmp_path


def _derived(rule: str, detector: RuleDetector, kind: str = "forbidden-pattern") -> DerivedRule:
    return DerivedRule(rule=rule, kind=kind, detector=detector, match_count=2, source="claude")


def test_dry_run_writes_nothing_but_returns_a_diff(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = write_derived_ruleset(root, [_derived("No print", RuleDetector(forbid=("print(",)))], dry_run=True)
    assert result.written is False
    assert "derived-no-print" in result.diff and "print(" in result.diff
    assert not (root / "guidelines" / "derived" / "derived.yml").exists()


def test_written_cache_round_trips_through_the_loader(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    derived = [
        _derived("Never use eval", RuleDetector(forbid_regex=(r"\beval\(",))),
        _derived("No hardcoded secret", RuleDetector(scan_checks=("secret-assignment",)), kind="content-scan"),
    ]
    result = write_derived_ruleset(root, derived, dry_run=False)

    assert result.written is True
    assert (root / "guidelines" / "derived" / "derived.yml").exists()
    instructions = load_yaml_guidelines(root)
    rules = {r for instr in instructions for r in instr.rules}
    assert "Never use eval" in rules
    assert "No hardcoded secret" in rules


def test_ids_are_unique_for_similar_rules(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    derived = [_derived("Same rule text", RuleDetector(forbid=("a",))) for _ in range(3)]
    write_derived_ruleset(root, derived, dry_run=False)
    # Loader raises on a duplicate id within one file — a clean load proves ids are unique.
    instructions = load_yaml_guidelines(root)
    assert any("Same rule text" in i.rules for i in instructions)
