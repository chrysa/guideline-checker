"""Tests for the generation loop's primary-detector pre-pass (spec §3.3).

``resolve_rule_detectors`` fills in a missing *primary* detector — one
absent from ``InstructionFile.rule_detectors`` — cache-first: a cache hit is
reused without re-deriving, a cache miss derives via ``derive_seed_rules``
and stores the result, and prose neither the cache nor the derivation
recognises is left out of ``rule_detectors`` (the rule stays advisory).

This pass is distinct from, and does not replace, ``_evaluate_rule``'s
always-on *supplementary* seed check in ``core/detection/__init__.py`` (see
the Task 6 controller ruling recorded there).
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.derive.cache import load, prose_hash, store
from guideline_checker.core.detection import resolve_rule_detectors
from guideline_checker.loader import InstructionFile, RuleDetector

_ENGINE_VERSION = "test-1.0.0"


def _instruction(rule: str) -> InstructionFile:
    return InstructionFile(
        path=Path("CLAUDE.md"),
        apply_to="**/*",
        description="test instruction",
        content=f"- {rule}",
        rules=[rule],
    )


def test_cache_hit_is_used_without_rederiving(tmp_path: Path) -> None:
    """A cache hit is reused as-is — no re-derivation happens."""
    rule = "No print statements in production code"
    key = prose_hash(rule, _ENGINE_VERSION)
    store(tmp_path, key, RuleDetector(forbid=("cached-marker",)))

    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)

    detector = resolved[0].rule_detectors.get(rule)
    assert detector is not None
    # The cached marker proves the cache value won, not a fresh derivation
    # (a fresh derive_seed_rules() call for this prose would produce
    # "print(", never "cached-marker").
    assert "cached-marker" in detector.forbid
    assert "print(" not in detector.forbid


def test_cache_miss_derives_and_stores(tmp_path: Path) -> None:
    """A cache miss falls back to derive_seed_rules() and stores the result."""
    rule = "No print statements in production code"
    key = prose_hash(rule, _ENGINE_VERSION)
    assert load(tmp_path, key) is None  # confirm this is genuinely a miss

    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)

    detector = resolved[0].rule_detectors.get(rule)
    assert detector is not None
    assert "print(" in detector.forbid

    # Second call must now hit the cache this call wrote.
    assert load(tmp_path, key) is not None


def test_unrecognised_prose_stays_advisory(tmp_path: Path) -> None:
    """Prose neither the cache nor derive_seed_rules recognises stays advisory."""
    rule = "Prefer composition over inheritance"
    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)
    assert rule not in resolved[0].rule_detectors


def test_declared_detector_is_left_untouched() -> None:
    """A rule that already has a declared detector is never touched by the pass."""
    rule = "No print statements in production code"
    declared = RuleDetector(forbid=("declared-marker",))
    instruction = InstructionFile(
        path=Path("CLAUDE.md"),
        apply_to="**/*",
        description="test instruction",
        content=f"- {rule}",
        rules=[rule],
        rule_detectors={rule: declared},
    )

    resolved = resolve_rule_detectors(Path("/nonexistent-root"), [instruction], _ENGINE_VERSION)

    assert resolved[0].rule_detectors[rule] is declared
