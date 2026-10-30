"""Tests for the proposer seam and the heuristic backend.

A ``Proposer`` turns a rule statement into a candidate ``detect:`` block. The
heuristic backend recycles the checker's own phrase table (``derive_seed_rules``) —
free, instant, and deterministic — so a dead rule whose prose the checker
already recognises gets an armed detector proposed with no LLM involved.
"""

from __future__ import annotations

from guideline_checker.loader import RuleDetector
from guideline_checker.workshop.proposer import HeuristicProposer, Proposal


def test_heuristic_proposes_a_detector_for_a_known_phrase() -> None:
    proposal = HeuristicProposer().propose("Never use print for debugging output")

    assert isinstance(proposal, Proposal)
    assert proposal.source == "heuristic"
    assert isinstance(proposal.detector, RuleDetector)
    assert "print(" in proposal.detector.forbid


def test_heuristic_returns_none_for_unrecognised_prose() -> None:
    # Prose the phrase table cannot map to any anti-pattern — the ai-models case.
    proposal = HeuristicProposer().propose("Structure prompts with XML tags for reliability")

    assert proposal is None


def test_heuristic_detector_dedupes_and_preserves_the_rule() -> None:
    proposal = HeuristicProposer().propose("no eval and no exec on runtime data")

    assert proposal is not None
    assert proposal.rule == "no eval and no exec on runtime data"
    # eval( and exec( are distinct patterns, each present once
    assert proposal.detector.forbid.count("eval(") == 1
    assert "exec(" in proposal.detector.forbid
