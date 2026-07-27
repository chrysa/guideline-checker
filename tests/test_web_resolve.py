"""Sync unit tests for the workshop's resolution/stats helpers.

The HTTP endpoints (/api/rules/resolve, /api/scan-all, /api/health-all) are
exercised by the async web suite; these cover the pure helpers so a regression
is caught even without the async test stack.
"""

from __future__ import annotations

from guideline_checker.rule_health import HealthState, RuleHealth
from guideline_checker.web.app import _is_resolvable, _serialize_health


def _health(rule: str, state: HealthState) -> RuleHealth:
    return RuleHealth(
        rule=rule,
        instruction="src.md",
        state=state,
        has_declarative_detector=False,
        has_phrase_detection=False,
        fire_count=0,
        reason="",
    )


def test_heuristic_mappable_dead_rule_is_resolvable() -> None:
    # "no print" is in the checker's phrase table -> the heuristic can arm it.
    assert _is_resolvable(_health("No print() calls in production", HealthState.DEAD)) is True


def test_semantic_rule_not_resolvable_without_llm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("GC_CLAUDE", raising=False)
    monkeypatch.delenv("GC_OLLAMA", raising=False)
    entry = _health("Structure prompts with XML tags for adherence", HealthState.DEAD)
    assert _is_resolvable(entry) is False


def test_proven_and_armed_rules_are_never_resolvable() -> None:
    assert _is_resolvable(_health("No print() calls", HealthState.PROVEN)) is False
    assert _is_resolvable(_health("No print() calls", HealthState.ARMED)) is False


def test_serialize_health_exposes_resolvable_flag() -> None:
    payload = _serialize_health([_health("No print() calls in production", HealthState.DEAD)])
    assert payload[0]["resolvable"] is True
    assert payload[0]["state"] == "dead"


def test_compliance_note_grades_by_violations() -> None:
    from guideline_checker.web.app import _compliance_note

    assert _compliance_note(0, 0, 0, 100)["grade"] == "A"
    assert _compliance_note(0, 0, 0, 100)["score"] == 100
    # Errors dominate and pull the grade down hard.
    assert _compliance_note(9, 91, 8, 541)["grade"] == "F"
    # A couple of warnings only: still a strong grade.
    assert _compliance_note(0, 3, 0, 100)["grade"] == "A"


def test_compliance_note_na_when_no_rules() -> None:
    from guideline_checker.web.app import _compliance_note

    note = _compliance_note(0, 0, 0, 0)
    assert note["grade"] == "n/a" and note["score"] is None
