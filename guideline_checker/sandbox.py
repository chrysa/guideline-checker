"""The sandbox — replay a proposed detector for proof, writing nothing.

The workshop never asks the user to trust a proposal blind: it replays the
candidate detector through the *real* per-file detection path
(``core.detection._check_file``) against the working tree and returns exactly what it
catches — file, line, excerpt. Deterministic, offline, read-only. This is the
proof shown before the proposal is ever written to ``guidelines/*.yml``
(see ADR D-0012).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.core.detection import _collect_files
from guideline_checker.core.detection.pattern import _matches_pattern
from guideline_checker.core.detection.presence import _declared_violations
from guideline_checker.loader import RuleDetector

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
