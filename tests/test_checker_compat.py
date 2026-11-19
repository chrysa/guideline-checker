"""The legacy ``guideline_checker.checker`` import path keeps resolving to the v2 engine."""

from __future__ import annotations

from guideline_checker import checker
from guideline_checker.core import detection
from guideline_checker.core.detection import pattern


def test_legacy_module_reexports_the_v2_engine() -> None:
    assert checker.run_checks is detection.run_checks
    assert checker.RuleResult is detection.RuleResult
    assert checker.Violation is detection.Violation
    assert checker.PatternCheck is pattern.PatternCheck


def test_legacy_module_declares_its_public_surface() -> None:
    assert sorted(checker.__all__) == ["PatternCheck", "RuleResult", "Violation", "run_checks"]
