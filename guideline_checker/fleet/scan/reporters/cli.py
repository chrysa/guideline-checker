"""Plain-text summary table printed at the end of a run."""

from __future__ import annotations

from guideline_checker.fleet.scan.models import Dimension, ScanReport
from guideline_checker.fleet.scan.reporters.formatting import pct, score_cell

_DIM_SHORT = {
    Dimension.CONFORMANCE: "conformance",
    Dimension.OPERATIONAL_READINESS: "operational",
    Dimension.FUNCTIONAL_MATURITY: "functional",
}


def render_cli(report: ScanReport, *, max_rows: int) -> str:
    scope = report.scope
    agg = report.aggregates
    lines = [
        f"fleet-scan run {report.run_id}  (rules {report.rules_version}, config {report.config_fingerprint[:12]})",
        f"scope: requested {len(scope.requested)} | collected {len(scope.collected)} | evaluated {len(scope.evaluated)}"
        f" | excluded {len(scope.excluded)} | errors {len(scope.errors)}",
    ]
    for dim, score in agg.dimensions.items():
        lines.append(f"fleet {_DIM_SHORT[dim]:<12} {score_cell(score)}")
    if agg.blocked_repos:
        lines.append(f"BLOCKED repos ({len(agg.blocked_repos)}): {', '.join(agg.blocked_repos)}")
    header = f"{'repo':<44} {'outcome':<9} " + " ".join(f"{_DIM_SHORT[d]:>12}" for d in Dimension) + "  blocking"
    lines += ["", header, "-" * len(header)]
    for result in report.repos[:max_rows]:
        cells = " ".join(f"{pct(result.scores[d].score) if d in result.scores else '-':>12}" for d in Dimension)
        blocking = ",".join(result.blocking_failures) or "-"
        lines.append(f"{result.repo[:44]:<44} {result.outcome.value:<9} {cells}  {blocking}")
    hidden = len(report.repos) - max_rows
    if hidden > 0:
        lines.append(f"... {hidden} more repo(s) in the JSON/Markdown reports")
    return "\n".join(lines)
