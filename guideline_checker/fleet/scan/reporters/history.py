"""Scan history: one JSON line per run in an index file, pruned to ``retention_runs`` entries.

Retention prunes index lines only; run directories are never deleted by the tool.
"""

from __future__ import annotations

import json
from pathlib import Path

from guideline_checker.fleet.scan.atomic_io import atomic_write_text
from guideline_checker.fleet.scan.models import ScanReport


class HistoryIndex:
    def __init__(self, *, path: Path, retention_runs: int) -> None:
        self._path = path
        self._retention = retention_runs

    def append(self, *, report: ScanReport, report_dir: Path) -> None:
        """Record ``report`` (replacing a previous line of the same run, e.g. after a resume)."""
        entries = [e for e in self.entries() if e.get("run_id") != report.run_id]
        entries.append(
            {
                "run_id": report.run_id,
                "started_at": report.started_at.isoformat(),
                "finished_at": report.finished_at.isoformat(),
                "rules_version": report.rules_version,
                "config_fingerprint": report.config_fingerprint,
                "resumed": report.resumed,
                "requested": len(report.scope.requested),
                "evaluated": len(report.scope.evaluated),
                "excluded": len(report.scope.excluded),
                "errors": len(report.scope.errors),
                "blocked_repos": len(report.aggregates.blocked_repos),
                "report_dir": str(report_dir),
            }
        )
        kept = entries[-self._retention :]
        atomic_write_text(path=self._path, text="".join(json.dumps(e, sort_keys=True) + "\n" for e in kept))

    def entries(self) -> list[dict[str, object]]:
        if not self._path.is_file():
            return []
        out: list[dict[str, object]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out
