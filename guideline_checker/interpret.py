"""Interpret-once — derive a kinded, proven ruleset from host prose (ADR D-0016).

The founding loop of the tool (D-0010→D-0016): read the host's prose **once**,
let the proposer map each sentence onto a mechanism (a detector, classified into
a :class:`~guideline_checker.core.detection.CheckKind`), and **replay it in the sandbox for
proof** — before anything is written. The LLM only proposes; the sandbox proves;
detection stays deterministic.

This module is the batch step that turns a set of prose rules (typically the
``advisory`` ones a scan surfaces but cannot yet enforce) into a list of
:class:`DerivedRule` — each carrying its source sentence (provenance), the
mechanism kind, the concrete detector, and how many lines it proved on. That list
is the per-repo derived cache D-0016 describes; nothing here writes it. Proposer
and sandbox are injected, so the interpretation is pure and unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from guideline_checker.core.detection import CheckKind, kind_of_detector
from guideline_checker.loader import RuleDetector
from guideline_checker.proposer import Proposal

# Bound the batch: interpreting prose escalates to an LLM per sentence, so a run
# over hundreds of advisory rules would be slow and costly. Callers pass a limit.
_DEFAULT_LIMIT = 25


@dataclass(frozen=True)
class DerivedRule:
    """One host sentence interpreted into a proven, kinded detector."""

    rule: str  # the host prose sentence — its own provenance
    kind: str  # CheckKind value
    detector: RuleDetector
    match_count: int  # lines the detector proved on in the sandbox
    source: str  # who proposed it: heuristic | claude | ollama


def interpret_rules(
    rules: Iterable[str],
    propose: Callable[[str], Proposal | None],
    replay: Callable[[str, RuleDetector], int],
    *,
    limit: int = _DEFAULT_LIMIT,
) -> list[DerivedRule]:
    """Interpret ``rules`` into derived, sandbox-proven rules (bounded by ``limit``).

    For each sentence: ``propose`` a detector (heuristic first, LLM fallback — the
    caller's concern), classify it into a kind, and ``replay`` it for proof. A
    sentence the proposer cannot map is skipped (it stays advisory). Order is
    preserved; duplicates are collapsed so the same sentence is interpreted once.

    ``limit`` bounds the number of sentences *attempted* (each attempt may cost an
    LLM call), not the number derived — so a run over prose that mostly does not
    map still makes at most ``limit`` proposals, never one per advisory rule.
    """
    derived: list[DerivedRule] = []
    seen: set[str] = set()
    attempts = 0
    for rule in rules:
        if attempts >= limit:
            break
        if rule in seen:
            continue
        seen.add(rule)
        attempts += 1
        proposal = propose(rule)
        if proposal is None:
            continue
        kind = kind_of_detector(proposal.detector) or CheckKind.FORBIDDEN_PATTERN
        match_count = replay(rule, proposal.detector)
        derived.append(
            DerivedRule(
                rule=rule,
                kind=kind.value,
                detector=proposal.detector,
                match_count=match_count,
                source=proposal.source,
            )
        )
    return derived
