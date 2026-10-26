"""Markdown reporter — consolidation (scope, scores, blocking, unknowns) + reprise (what to resume)."""

from __future__ import annotations

from guideline_checker.fleet.scan.models import REVIVAL_CAVEAT, Dimension, RepoOutcome, ScanReport, Status
from guideline_checker.fleet.scan.reporters.formatting import pct, score_cell

_MAX_LIST = 200


# API-sourced text (tree paths, CI conclusions, error messages) must not inject links,
# images, HTML or table breaks into the rendered report.
_CELL_ESCAPES = str.maketrans(
    {
        "\\": "\\\\",
        "|": "\\|",
        "`": "\\`",
        "[": "\\[",
        "]": "\\]",
        "<": "&lt;",
        ">": "&gt;",
        "\r": " ",
        "\n": " ",
    }
)


def _cell(text: str) -> str:
    return text.translate(_CELL_ESCAPES)


def render_markdown(report: ScanReport) -> str:
    sections = [
        _header(report),
        _scope(report),
        _fleet(report),
        _blocking(report),
        _repos(report),
        _unknowns(report),
        _errors(report),
        _revival(report),
        _reprise(report),
    ]
    return "\n\n".join(s for s in sections if s) + "\n"


def _header(report: ScanReport) -> str:
    return "\n".join(
        [
            f"# Fleet scan — run `{report.run_id}`",
            "",
            f"- started: {report.started_at.isoformat()} · finished: {report.finished_at.isoformat()}",
            f"- tool: {report.tool_version} · rules: {report.rules_version} (`{report.rules_sha256[:12]}`)",
            f"- config fingerprint: `{report.config_fingerprint}`",
            f"- resumed: {'yes' if report.resumed else 'no'} · forced config change: "
            f"{'YES — results mix configurations' if report.forced_config_change else 'no'}",
            "",
            "Scores: `score = pass/(pass+fail)`, `coverage = (pass+fail)/applicable`; "
            "unknown and error never count as pass. Dimensions are never merged.",
        ]
    )


def _scope(report: ScanReport) -> str:
    scope = report.scope
    lines = [
        "## Scope",
        "",
        "| requested | collected | evaluated | excluded | errors |",
        "|---|---|---|---|---|",
        f"| {len(scope.requested)} | {len(scope.collected)} | {len(scope.evaluated)} | {len(scope.excluded)} "
        f"| {len(scope.errors)} |",
    ]
    if scope.excluded:
        lines += ["", "### Excluded", "", "| repo | reason |", "|---|---|"]
        lines += [f"| {e.repo} | {_cell(e.reason)} |" for e in scope.excluded]
    if scope.errors:
        lines += ["", "### Repositories in error", "", "| repo | kind | message |", "|---|---|---|"]
        lines += [f"| {e.repo} | {e.kind.value} | {_cell(e.message)} |" for e in scope.errors]
    return "\n".join(lines)


def _fleet(report: ScanReport) -> str:
    agg = report.aggregates
    lines = [
        "## Fleet totals",
        "",
        "| dimension | score | coverage | pass | fail | unknown | error | n/a | applicable |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for dim, s in agg.dimensions.items():
        lines.append(
            f"| {dim.value} | {pct(s.score)} | {pct(s.coverage)} | {s.passed} | {s.fail} | {s.unknown} | {s.error} "
            f"| {s.not_applicable} | {s.applicable} |"
        )
    if agg.error_kinds:
        kinds = ", ".join(f"{k.value}: {v}" for k, v in sorted(agg.error_kinds.items()))
        lines += ["", f"Collection errors by kind: {kinds}"]
    return "\n".join(lines)


def _blocking(report: ScanReport) -> str:
    blocked = [r for r in report.repos if r.blocked]
    lines = ["## Blocking failures", ""]
    if not blocked:
        return "\n".join([*lines, "None."])
    lines.append("Listed whatever the score — an average never hides a blocking failure.")
    lines.append("")
    lines += [f"- **{r.repo}**: {', '.join(r.blocking_failures)}" for r in blocked]
    return "\n".join(lines)


def _repos(report: ScanReport) -> str:
    header = "| repo | project | outcome | commit | profiles | " + " | ".join(d.value for d in Dimension) + " |"
    lines = ["## Per repository", "", header, "|" + "---|" * (5 + len(Dimension))]
    for r in report.repos:
        cells = " | ".join(score_cell(r.scores.get(d)) for d in Dimension)
        sha = (r.commit_sha or "-")[:10]
        profiles = ", ".join(r.profiles) + (" (incomplete)" if r.profiles_status is Status.UNKNOWN else "")
        lines.append(f"| {r.repo} | {r.project_id or '-'} | {r.outcome.value} | {sha} | {profiles or '-'} | {cells} |")
    return "\n".join(lines)


def _unknowns(report: ScanReport) -> str:
    rows = [(r.repo, c) for r in report.repos for c in r.checks if c.status is Status.UNKNOWN]
    if not rows:
        return "## Unknowns\n\nNone."
    lines = ["## Unknowns", "", "| repo | rule | reason |", "|---|---|---|"]
    lines += [f"| {repo} | {c.rule_id} | {_cell(c.message)} |" for repo, c in rows[:_MAX_LIST]]
    if len(rows) > _MAX_LIST:
        lines.append(f"\n… {len(rows) - _MAX_LIST} more in report.json")
    return "\n".join(lines)


def _errors(report: ScanReport) -> str:
    rows = [(r.repo, c) for r in report.repos for c in r.checks if c.status is Status.ERROR]
    if not rows:
        return ""
    lines = ["## Check errors", "", "| repo | rule | kind | message |", "|---|---|---|---|"]
    lines += [
        f"| {repo} | {c.rule_id} | {c.error_kind.value if c.error_kind else '-'} | {_cell(c.message)} |"
        for repo, c in rows[:_MAX_LIST]
    ]
    return "\n".join(lines)


def _revival(report: ScanReport) -> str:
    lines = ["## Revival candidates", "", f"> {REVIVAL_CAVEAT.capitalize()}: observable foundations only."]
    if not report.revival_candidates:
        return "\n".join([*lines, "", "None computed."])
    lines += ["", "| rank | repo | points | signals |", "|---|---|---|---|"]
    for c in report.revival_candidates:
        signals = ", ".join(f"{s.name}={s.status.value}" for s in c.signals)
        lines.append(f"| {c.rank} | {c.repo} | {c.signal_points:g} | {signals} |")
    return "\n".join(lines)


def _reprise(report: ScanReport) -> str:
    retry = [r.repo for r in report.repos if r.outcome is RepoOutcome.ERROR and not r.is_final]
    lines = ["## Reprise", ""]
    if retry:
        lines.append(
            f"{len(retry)} repo(s) failed for a retryable reason; resume with `make scan-resume RUN={report.run_id}`:"
        )
        lines += [f"- {repo}" for repo in retry]
    else:
        lines.append("Nothing to resume.")
    lines += ["", f"Regenerate these reports with `make scan-report RUN={report.run_id}`."]
    return "\n".join(lines)
