"""Write the enabled reports of a run (JSON, Markdown, history) and return the CLI summary."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.fleet.scan.models import ReportersConfig, ScanReport
from guideline_checker.fleet.scan.reporters.cli import render_cli
from guideline_checker.fleet.scan.reporters.history import HistoryIndex
from guideline_checker.fleet.scan.reporters.json_out import render_json
from guideline_checker.fleet.scan.reporters.markdown import render_markdown
from guideline_checker.fleet.scan.run_store import RunStore


@dataclass
class ReportPublisher:
    config: ReportersConfig
    history_path: Path
    written: list[Path] = field(default_factory=list)
    summary: str = ""

    def __call__(self, report: ScanReport, store: RunStore) -> None:
        self.written = []
        if self.config.json_report.enabled:
            self.written.append(store.write_text(filename=self.config.json_report.filename, text=render_json(report)))
        if self.config.markdown.enabled:
            self.written.append(store.write_text(filename=self.config.markdown.filename, text=render_markdown(report)))
        if self.config.history.enabled:
            HistoryIndex(path=self.history_path, retention_runs=self.config.history.retention_runs).append(
                report=report, report_dir=store.run_dir
            )
        self.summary = render_cli(report, max_rows=self.config.cli.max_rows) if self.config.cli.enabled else ""
