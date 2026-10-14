"""Backward-compatible entry point: the engine lives in ``guideline_checker.core.detection``."""

from __future__ import annotations

from guideline_checker.core.detection import RuleResult, Violation, run_checks
from guideline_checker.core.detection.pattern import PatternCheck

__all__ = ["PatternCheck", "RuleResult", "Violation", "run_checks"]
