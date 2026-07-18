"""The proposer seam — turn a rule statement into a candidate ``detect:`` block.

A ``Proposer`` proposes; it never judges. Every proposal is replayed by the
deterministic engine in the sandbox and shown with its proof before any write
(see ``sandbox.replay`` and ADR D-0012). The LLM backends (Ollama, Claude) live
behind the optional ``[assist]`` extra; the ``HeuristicProposer`` here needs no
extra and no network — it recycles the checker's own phrase table, so a dead
rule whose prose the checker already recognises gets an armed detector for free,
tried before any model is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from guideline_checker.checker import _build_checks
from guideline_checker.loader import RuleDetector


@dataclass(frozen=True)
class Proposal:
    """A candidate detector for a rule, plus why it was proposed and by whom."""

    rule: str
    detector: RuleDetector
    rationale: str
    source: str  # "heuristic" | "ollama" | "claude"


@runtime_checkable
class Proposer(Protocol):
    """A backend that proposes a detector for a rule, or ``None`` if it cannot."""

    source: str

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None: ...


class HeuristicProposer:
    """Propose a detector by recycling the checker's phrase table — no LLM.

    If ``_build_checks`` recognises the rule's prose, its anti-pattern substrings
    become the proposal's ``forbid`` list. Rules the table cannot map (e.g. the
    ``ai-models/`` conventions) return ``None`` so the router escalates to an LLM.
    """

    source = "heuristic"

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None:
        checks = _build_checks(rule.lower())
        if not checks:
            return None
        forbid = tuple(dict.fromkeys(check.pattern for check in checks))
        match_in_comments = any(check.match_in_comments for check in checks)
        joined = ", ".join(forbid)
        return Proposal(
            rule=rule,
            detector=RuleDetector(forbid=forbid, match_in_comments=match_in_comments),
            rationale=f"Recognised {len(forbid)} known anti-pattern substring(s): {joined}.",
            source=self.source,
        )
