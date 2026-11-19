"""Shared number formatting for the text reporters (CLI + Markdown)."""

from __future__ import annotations

from guideline_checker.fleet.scan.models import DimensionScore

NOT_AVAILABLE = "n/a"


def pct(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value * 100:.1f}%"


def score_cell(score: DimensionScore | None) -> str:
    """``score (pass/decided) · cov coverage (decided/applicable)`` — denominators always shown."""
    if score is None:
        return "-"
    decided = score.passed + score.fail
    blocked = " BLOCKED" if score.blocked else ""
    coverage = f"cov {pct(score.coverage)} ({decided}/{score.applicable})"
    return f"{pct(score.score)} ({score.passed}/{decided}) {coverage}{blocked}"
