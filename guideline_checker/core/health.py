"""Deterministic rule-health engine.

Answers the question a green scan never can: *is a rule capable of detecting
anything, and does it actually fire?* Computed with no LLM and no network.

A rule is detectable when it carries a declarative ``detect:`` block **or** its
prose maps to a phrase-derived check (``derive_seed_rules``, plus the
presence/length triggers). A rule with neither is ``DEAD``: it can never flag a
violation, however green the scan looks. This is what makes the referential
honest — a YAML rule advertised as enforceable but carrying no detector surfaces
as dead instead of hiding behind an empty violation list.

See ``docs/superpowers/specs/2026-07-18-rule-health-workshop-design.md``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from guideline_checker.core.derive.seed import derive_seed_rules
from guideline_checker.core.detection import CheckKind, _collect_files, kind_of_detector, kind_of_phrase
from guideline_checker.core.detection.pattern import _matches_pattern
from guideline_checker.core.detection.presence import _declared_violations
from guideline_checker.loader import RuleDetector, SourceType

if TYPE_CHECKING:
    from guideline_checker.core.detection import RuleResult
    from guideline_checker.loader import InstructionFile

# Text-only triggers for the presence/length checks that ``derive_seed_rules``
# does not cover. Kept in sync with ``presence._check_presence_rules`` /
# ``numeric._check_length_rules`` — they gate on file suffix/content at scan
# time, but the rule text alone is enough to know the rule is *armed*.
_FUTURE_ANNOTATIONS = "from __future__ import annotations"
_MAX_FUNCTION_LENGTH = (
    re.compile(r"max\s+function\s+length[:\s]+(\d+)"),
    re.compile(r"max\s+(\d+)\s+lines?\s+(?:per\s+)?function"),
)
_MAX_FILE_LENGTH = (
    re.compile(r"max(?:imum)?\s+file\s+length[:\s]+(\d+)"),
    re.compile(r"max\s+(\d+)\s+lines?\s+per\s+file"),
)


class HealthState(StrEnum):
    """The lifecycle state of a single rule's detection capability."""

    PROVEN = "proven"  # detectable, and fires on real code in this repo
    ARMED = "armed"  # detectable, fires nowhere (repo is clean)
    DEAD = "dead"  # YAML rule advertised as enforceable but with no detector -> defect
    ADVISORY = "advisory"  # undetectable markdown guidance -> surfaced, never enforced
    SUSPECT = "suspect"  # fires only on suppressed / baselined lines


@dataclass(frozen=True)
class RuleHealth:
    """A rule's detection capability and, if a scan ran, whether it fired."""

    rule: str
    instruction: str
    state: HealthState
    has_declarative_detector: bool
    has_phrase_detection: bool
    fire_count: int
    reason: str
    # ADR D-0016: the host prose sentence this rule derives from ("" if none).
    provenance: str = ""
    # ADR D-0020: the generic mechanism (kind) this rule is checked by.
    kind: str = CheckKind.ADVISORY.value


def compute_rule_health(
    instructions: list[InstructionFile],
    results: list[RuleResult] | None = None,
) -> list[RuleHealth]:
    """Compute health for every rule across all instruction sources.

    Without ``results`` every detectable rule is reported ``ARMED`` (fired state
    unknown); ``DEAD`` rules never depend on a scan. With ``results`` the fired
    rules are upgraded to ``PROVEN`` and the rest stay ``ARMED``.
    """
    fired = _fired_counts(results) if results else Counter()
    return [
        _rule_health(rule, instruction.path.name, instruction, fired)
        for instruction in instructions
        for rule in instruction.rules
    ]


def summarize(health: list[RuleHealth]) -> dict[str, int]:
    """Roll health up to per-state counts (stable keys for the API/front)."""
    counts = Counter(entry.state.value for entry in health)
    return {state.value: counts.get(state.value, 0) for state in HealthState}


def _fired_counts(results: list[RuleResult]) -> Counter[str]:
    """Count violations per rule string across all scan results."""
    counts: Counter[str] = Counter()
    for result in results:
        for violation in result.violations:
            counts[violation.rule] += 1
    return counts


def _has_phrase_detection(rule: str) -> bool:
    """True when the rule's prose maps to any phrase-derived check."""
    low = rule.lower()
    if derive_seed_rules(low) is not None:
        return True
    if _FUTURE_ANNOTATIONS in low:
        return True
    if "/health" in low and "mandatory" in low:
        return True
    return any(pattern.search(low) for pattern in (*_MAX_FUNCTION_LENGTH, *_MAX_FILE_LENGTH))


def _rule_health(
    rule: str,
    source: str,
    instruction: InstructionFile,
    fired: Counter[str],
) -> RuleHealth:
    has_detector = rule in instruction.rule_detectors
    has_phrase = _has_phrase_detection(rule)
    fire_count = fired[rule]
    provenance = instruction.rule_provenance.get(rule, "")

    if not has_detector and not has_phrase:
        is_yaml = instruction.source_type is SourceType.GUIDELINES_YAML
        state = HealthState.DEAD if is_yaml else HealthState.ADVISORY
        reason = (
            "YAML rule advertised as enforceable but carries no detector — fix or remove it."
            if is_yaml
            else "Markdown guidance with no recognised detector — surfaced, never enforced."
        )
        return RuleHealth(
            rule=rule,
            instruction=source,
            state=state,
            has_declarative_detector=False,
            has_phrase_detection=False,
            fire_count=0,
            reason=reason,
            provenance=provenance,
            kind=CheckKind.ADVISORY.value,
        )

    kind = kind_of_detector(instruction.rule_detectors.get(rule)) if has_detector else kind_of_phrase(rule)

    mechanism = "declarative detector" if has_detector else "phrase-derived check"
    if fire_count > 0:
        state, reason = HealthState.PROVEN, f"Fires on {fire_count} line(s) via {mechanism}."
    else:
        state, reason = HealthState.ARMED, f"Valid {mechanism}, no match in the current scan."

    return RuleHealth(
        rule=rule,
        instruction=source,
        state=state,
        has_declarative_detector=has_detector,
        has_phrase_detection=has_phrase,
        fire_count=fire_count,
        reason=reason,
        provenance=provenance,
        kind=(kind or CheckKind.FORBIDDEN_PATTERN).value,
    )


# The proof routine below was guideline_checker/sandbox.py before the v2 redesign
# (spec §4: "health proves it in sandbox — core sandbox path, read-only"). It has
# no LLM/gh dependency, so it is core-owned; workshop/proposer.py imports it from
# here rather than keeping its own copy (satellite → core, never the reverse).
#
# The sandbox — replay a proposed detector for proof, writing nothing.
#
# The workshop never asks the user to trust a proposal blind: it replays the
# candidate detector through the *real* per-file detection path
# (``core.detection._check_file``) against the working tree and returns exactly what it
# catches — file, line, excerpt. Deterministic, offline, read-only. This is the
# proof shown before the proposal is ever written to ``guidelines/*.yml``
# (see ADR D-0012).

_MAX_HITS = 100


@dataclass(frozen=True)
class Hit:
    """One line a replayed detector flagged."""

    file: str
    line: int
    excerpt: str


@dataclass(frozen=True)
class Proof:
    """What a proposed detector catches on this repo — the evidence to validate."""

    rule: str
    match_count: int
    files_scanned: int
    hits: list[Hit] = field(default_factory=list)


def replay(
    rule: str,
    detector: RuleDetector,
    root: Path,
    apply_to: str = "**/*",
    max_hits: int = _MAX_HITS,
) -> Proof:
    """Run ``detector`` for ``rule`` over ``root`` and return the lines it flags.

    Writes nothing. Runs the proposed detector in isolation through the engine's
    declarative path (``_declared_violations``), so the proof is what *this*
    detector would report — free of the rule's incidental phrase-matching.
    ``hits`` is capped at ``max_hits`` while ``match_count`` stays the true total.
    """
    hits: list[Hit] = []
    match_count = 0
    files_scanned = 0
    for file_path in _collect_files(root):
        if not _matches_pattern(file_path, root, apply_to):
            continue
        files_scanned += 1
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for violation in _declared_violations(file_path, lines, rule, detector, root):
            match_count += 1
            if len(hits) < max_hits:
                hits.append(
                    Hit(
                        file=_relativize(violation.file, root),
                        line=violation.line_number,
                        excerpt=violation.line_content,
                    )
                )

    return Proof(rule=rule, match_count=match_count, files_scanned=files_scanned, hits=hits)


def _relativize(file_path: Path, root: Path) -> str:
    """Best-effort repo-relative path for display."""
    try:
        return str(file_path.relative_to(root))
    except ValueError:
        return str(file_path)
