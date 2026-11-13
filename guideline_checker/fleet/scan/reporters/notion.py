"""Notion reporter — a disabled stub. Writing to Notion is out of the MVP scope.

The configuration model only accepts ``notion.enabled: false``; calling :meth:`publish`
anyway raises :class:`ReporterDisabledError` so no code path can write silently.
"""

from __future__ import annotations

from guideline_checker.fleet.scan.errors import ReporterDisabledError
from guideline_checker.fleet.scan.models import NotionReporterConfig, ScanReport


class NotionReporter:
    def __init__(self, *, config: NotionReporterConfig) -> None:
        self._config = config

    def publish(self, report: ScanReport) -> None:
        raise ReporterDisabledError(
            f"Notion reporter is disabled ({self._config.reason}); run {report.run_id} was not published"
        )
