"""Tests for interpret-once (ADR D-0016) — deriving a kinded ruleset from prose."""

from __future__ import annotations

from guideline_checker.interpret import DerivedRule, interpret_rules
from guideline_checker.loader import RuleDetector
from guideline_checker.proposer import Proposal


def _proposal(rule: str, detector: RuleDetector, source: str = "claude") -> Proposal:
    return Proposal(rule=rule, detector=detector, rationale="", source=source)


def test_interprets_mappable_rules_and_skips_the_rest() -> None:
    def propose(rule: str) -> Proposal | None:
        if "print" in rule:
            return _proposal(rule, RuleDetector(forbid=("print(",)))
        return None  # not mechanically mappable

    def replay(rule: str, detector: RuleDetector) -> int:
        return 3

    derived = interpret_rules(["No print() calls", "Be kind to reviewers"], propose, replay)

    assert len(derived) == 1
    d = derived[0]
    assert isinstance(d, DerivedRule)
    assert d.rule == "No print() calls"
    assert d.kind == "forbidden-pattern"
    assert d.match_count == 3
    assert d.source == "claude"


def test_classifies_kind_from_detector() -> None:
    def propose(rule: str) -> Proposal | None:
        return _proposal(rule, RuleDetector(ast_checks=("pydantic-v1",)))

    derived = interpret_rules(["Use Pydantic v2"], propose, lambda r, d: 0)
    assert derived[0].kind == "ast-structure"


def test_limit_bounds_the_batch() -> None:
    rules = [f"rule {i}" for i in range(50)]
    derived = interpret_rules(rules, lambda r: _proposal(r, RuleDetector(forbid=("x",))), lambda r, d: 1, limit=5)
    assert len(derived) == 5


def test_duplicate_sentences_are_interpreted_once() -> None:
    calls: list[str] = []

    def propose(rule: str) -> Proposal | None:
        calls.append(rule)
        return _proposal(rule, RuleDetector(forbid=("x",)))

    derived = interpret_rules(["same", "same", "same"], propose, lambda r, d: 1)
    assert len(derived) == 1
    assert calls == ["same"]
