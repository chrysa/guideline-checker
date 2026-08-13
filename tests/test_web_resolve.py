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


def test_semantic_dead_rule_not_resolvable_without_backend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # GC_CLAUDE=0 opts out of the Claude CLI auto-detect; no Ollama either.
    monkeypatch.setenv("GC_CLAUDE", "0")
    monkeypatch.delenv("GC_OLLAMA", raising=False)
    entry = _health("Structure prompts with XML tags for adherence", HealthState.DEAD)
    assert _is_resolvable(entry) is False


def test_proven_armed_and_advisory_rules_are_never_resolvable() -> None:
    # Only dead YAML rules are one-click resolvable (advisory is markdown — nothing to write to).
    assert _is_resolvable(_health("No print() calls", HealthState.PROVEN)) is False
    assert _is_resolvable(_health("No print() calls", HealthState.ARMED)) is False
    assert _is_resolvable(_health("No print() calls", HealthState.ADVISORY)) is False


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


# ── Folder browser: the path stays bounded to the browse root ────────────────


def test_within_base_accepts_the_base_and_its_children(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from guideline_checker.web.app import _within_base

    base = tmp_path.resolve()
    (base / "sub").mkdir()
    assert _within_base(base, ".") == base
    assert _within_base(base, "sub") == base / "sub"


def test_within_base_rejects_traversal_and_outside_paths(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from guideline_checker.web.app import _within_base

    base = (tmp_path / "root").resolve()
    base.mkdir()
    (tmp_path / "sibling").mkdir()
    assert _within_base(base, "../sibling") is None
    assert _within_base(base, str(tmp_path / "sibling")) is None
    assert _within_base(base, "..") is None


def test_browse_listing_lists_visible_subdirs_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from guideline_checker.web.app import _browse_listing

    base = tmp_path.resolve()
    (base / "keep").mkdir()
    (base / ".hidden").mkdir()
    (base / "a-file.txt").write_text("x", encoding="utf-8")
    listing = _browse_listing(base, None)
    assert [e["name"] for e in listing["entries"]] == ["keep"]
    assert listing["parent"] is None  # at the root the UI cannot climb out
    assert listing["cwd"] == str(base)


def test_browse_listing_reports_parent_below_the_root(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from guideline_checker.web.app import _browse_listing

    base = tmp_path.resolve()
    (base / "sub").mkdir()
    listing = _browse_listing(base, "sub")
    assert listing["parent"] == str(base)


def test_browse_listing_rejects_a_path_outside_the_root(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pytest
    from fastapi import HTTPException

    from guideline_checker.web.app import _browse_listing

    base = (tmp_path / "root").resolve()
    base.mkdir()
    (tmp_path / "sibling").mkdir()
    with pytest.raises(HTTPException):
        _browse_listing(base, "../sibling")


def test_browse_listing_flags_a_scannable_directory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from guideline_checker.web.app import _browse_listing

    base = tmp_path.resolve()
    (base / "CLAUDE.md").write_text("# rules", encoding="utf-8")
    assert _browse_listing(base, None)["scannable"] is True
