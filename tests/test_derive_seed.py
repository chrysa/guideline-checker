"""Tests for core/derive/seed.py — the phrase-table-as-heuristic seed translator."""

from __future__ import annotations

from guideline_checker.core.derive.seed import derive_seed_rules


def test_no_print_phrase_derives_a_forbid_pattern_detector() -> None:
    detector = derive_seed_rules("No print statements in production code")
    assert detector is not None
    assert "print(" in detector.forbid


def test_unrecognised_prose_derives_nothing() -> None:
    assert derive_seed_rules("Prefer composition over inheritance") is None
