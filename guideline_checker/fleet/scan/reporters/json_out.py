"""JSON reporter — the :class:`ScanReport` contract, serialised by pydantic."""

from __future__ import annotations

from guideline_checker.fleet.scan.models import ScanReport


def render_json(report: ScanReport) -> str:
    return report.model_dump_json(indent=2)
