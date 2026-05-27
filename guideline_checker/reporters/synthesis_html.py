"""Multi-repo synthesis HTML reporter.

Generates a single ``guideline-synthesis.html`` that aggregates results from
all repos in a workspace directory, with:

- A per-repo status table (PASS / FAIL / SKIP) with links to individual reports
- A processing-status checklist showing which repos were scanned vs. skipped
- Aggregated error/warning counts
- Top violated rules and most-affected files across the workspace
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from guideline_checker.reporters.html import _escape_html

# ─── Template ─────────────────────────────────────────────────────────────────

_SYNTHESIS_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Guideline Compliance — Workspace Synthesis</title>
<style>
:root {{
  --clr-bg: #0f1117; --clr-surface: #1a1d27; --clr-border: #2a2d3e;
  --clr-muted: #8b8fa8; --clr-text: #e2e4f0; --clr-heading: #fff;
  --clr-ok: #22c55e; --clr-err: #ef4444; --clr-warn: #f59e0b; --clr-info: #3b82f6;
  --clr-skip: #6b7280;
}}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 0;
  background: var(--clr-bg); color: var(--clr-text); min-height: 100vh; }}
a {{ color: var(--clr-info); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
header {{ background: linear-gradient(135deg, #1a1d27 0%, #12151f 100%);
  border-bottom: 1px solid var(--clr-border); padding: 2rem;
  text-align: center; }}
header h1 {{ margin: 0 0 .4rem; font-size: 1.6rem; color: var(--clr-heading); }}
header p {{ margin: 0; color: var(--clr-muted); font-size: .875rem; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }}

/* Stats bar */
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1rem; margin-bottom: 2rem; }}
.stat-card {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 10px; padding: 1.2rem; text-align: center; }}
.stat-card .value {{ font-size: 2rem; font-weight: 800; line-height: 1; }}
.stat-card .label {{ font-size: .72rem; color: var(--clr-muted); text-transform: uppercase;
  letter-spacing: .06em; margin-top: .3rem; }}
.stat-card.ok .value {{ color: var(--clr-ok); }}
.stat-card.error .value {{ color: var(--clr-err); }}
.stat-card.warning .value {{ color: var(--clr-warn); }}
.stat-card.info .value {{ color: var(--clr-info); }}
.stat-card.neutral .value {{ color: var(--clr-muted); }}

/* Section */
.section {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 10px; margin-bottom: 1.5rem; overflow: hidden; }}
.section-title {{ padding: .8rem 1.25rem; background: #1e2130;
  font-size: .9rem; font-weight: 700; color: var(--clr-heading);
  border-bottom: 1px solid var(--clr-border);
  display: flex; align-items: center; gap: .5rem; }}

/* Table */
table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
th {{ padding: .5rem 1rem; text-align: left; background: #1e2130;
  color: var(--clr-muted); font-size: .72rem; text-transform: uppercase;
  letter-spacing: .05em; border-bottom: 1px solid var(--clr-border); }}
td {{ padding: .55rem 1rem; border-bottom: 1px solid var(--clr-border); vertical-align: middle; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: rgba(255,255,255,.03); }}

/* Badges */
.badge {{ display: inline-block; padding: .2em .6em; border-radius: 4px;
  font-size: .72rem; font-weight: 700; white-space: nowrap; }}
.badge-ok {{ background: rgba(34,197,94,.15); color: #22c55e; border: 1px solid rgba(34,197,94,.3); }}
.badge-error {{ background: rgba(239,68,68,.15); color: #ef4444; border: 1px solid rgba(239,68,68,.3); }}
.badge-warning {{ background: rgba(245,158,11,.15); color: #f59e0b; border: 1px solid rgba(245,158,11,.3); }}
.badge-skip {{ background: rgba(107,114,128,.15); color: #9ca3af; border: 1px solid rgba(107,114,128,.3); }}
.badge-pending {{ background: rgba(59,130,246,.12); color: #60a5fa; border: 1px solid rgba(59,130,246,.25); }}

/* Status list */
.status-list {{ padding: .75rem 1.25rem; display: flex; flex-direction: column; gap: .5rem; }}
.status-item {{ display: flex; align-items: center; gap: .75rem; font-size: .84rem; }}
.status-icon {{ width: 1.1rem; text-align: center; flex-shrink: 0; }}
.status-label {{ flex: 1; }}
.status-label a {{ font-weight: 600; }}

/* Top tables */
.top-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }}
@media (max-width: 768px) {{ .top-grid {{ grid-template-columns: 1fr; }} }}
.bar-bg {{ background: rgba(59,130,246,.12); border-radius: 3px; height: 6px; width: 100%; }}
.bar-fill {{ height: 6px; border-radius: 3px; background: var(--clr-info); }}
.bar-fill.err {{ background: var(--clr-err); }}

/* Footer */
footer {{ text-align: center; color: var(--clr-muted); font-size: .75rem;
  padding: 2rem; border-top: 1px solid var(--clr-border); }}
</style>
</head>
<body>
<header>
  <h1>&#128203; Guideline Compliance — Workspace Synthesis</h1>
  <p>&#128193; {workspace} &nbsp;&mdash;&nbsp; &#128197; {generated_at}</p>
</header>
<div class="container">

<!-- Stats bar -->
<div class="stats-grid">
  <div class="stat-card neutral"><div class="value">{total_repos}</div><div class="label">Repos</div></div>
  <div class="stat-card ok"><div class="value">{repos_pass}</div><div class="label">Passing</div></div>
  <div class="stat-card error"><div class="value">{repos_fail}</div><div class="label">Failing</div></div>
  <div class="stat-card neutral"><div class="value">{repos_skip}</div><div class="label">Skipped</div></div>
  <div class="stat-card error"><div class="value">{total_errors}</div><div class="label">Total errors</div></div>
  <div class="stat-card warning"><div class="value">{total_warnings}</div><div class="label">Total warnings</div></div>
</div>

<!-- Repo status table -->
<div class="section">
  <div class="section-title">&#128202; Repository Status</div>
  <table>
    <thead>
      <tr>
        <th>Repository</th>
        <th>Status</th>
        <th>Report</th>
        <th style="text-align:right">&#128308; Errors</th>
        <th style="text-align:right">&#128993; Warnings</th>
        <th>Processed</th>
      </tr>
    </thead>
    <tbody>
{repo_rows}
    </tbody>
  </table>
</div>

<!-- Processing status checklist -->
<div class="section">
  <div class="section-title">&#9989; Processing Status</div>
  <div class="status-list">
{status_items}
  </div>
</div>

<!-- Top rules / top files -->
<div class="top-grid">
  <div class="section">
    <div class="section-title">&#128270; Top Violated Rules</div>
    <table>
      <thead><tr><th>Rule</th><th style="text-align:right">Count</th><th>Bar</th></tr></thead>
      <tbody>{top_rules_rows}</tbody>
    </table>
  </div>
  <div class="section">
    <div class="section-title">&#128196; Most Affected Files</div>
    <table>
      <thead><tr><th>File</th><th style="text-align:right">Violations</th><th>Bar</th></tr></thead>
      <tbody>{top_files_rows}</tbody>
    </table>
  </div>
</div>

</div>
<footer>Generated by <a href="https://github.com/chrysa/guideline-checker">guideline-checker</a></footer>
</body>
</html>
"""


# ─── Reporter ─────────────────────────────────────────────────────────────────


class SynthesisHtmlReporter:
    """Write a multi-repo synthesis HTML to *output_path*."""

    def write(
        self,
        workspace: Path,
        repo_entries: list[dict[str, object]],
        output_path: Path,
    ) -> None:
        """Generate the synthesis report.

        Args:
            workspace: The workspace root directory.
            repo_entries: List of dicts as produced by ``_cmd_synthesize``
                in ``cli.py``.  Each entry has at minimum:
                ``{"name": str, "path": Path, "skipped": bool}``.
                Processed entries additionally have:
                ``{"results": list[RuleResult], "linter_results": list[LinterResult],
                   "report_path": Path, "errors": int, "warnings": int}``.
            output_path: Where to write the synthesis HTML.
        """
        generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

        total_repos = len(repo_entries)
        repos_pass = 0
        repos_fail = 0
        repos_skip = 0
        total_errors = 0
        total_warnings = 0

        # Aggregate top-rules and top-files across all repos
        rule_counter: Counter[str] = Counter()
        file_counter: Counter[str] = Counter()

        repo_rows_parts: list[str] = []
        status_items_parts: list[str] = []

        for entry in repo_entries:
            name = entry["name"]
            skipped = entry.get("skipped", False)

            if skipped:
                repos_skip += 1
                reason = _escape_html(entry.get("reason", "unknown"))
                repo_rows_parts.append(
                    f"<tr>"
                    f"<td><strong>{_escape_html(name)}</strong></td>"
                    f"<td><span class='badge badge-skip'>SKIP</span></td>"
                    f"<td><span style='color:var(--clr-muted);font-size:.8rem'>{reason}</span></td>"
                    f"<td style='text-align:right'>—</td>"
                    f"<td style='text-align:right'>—</td>"
                    f"<td><span class='badge badge-skip'>skipped</span></td>"
                    f"</tr>"
                )
                status_items_parts.append(
                    f'<div class="status-item">'
                    f'<span class="status-icon">&#9940;</span>'
                    f'<span class="status-label"><strong>{_escape_html(name)}</strong>'
                    f' <span style="color:var(--clr-muted);font-size:.8rem">({reason})</span></span>'
                    f'<span class="badge badge-skip">skipped</span>'
                    f"</div>"
                )
                continue

            errors = entry.get("errors", 0)
            warnings = entry.get("warnings", 0)
            total_errors += errors
            total_warnings += warnings
            report_path: Path = entry.get("report_path", entry["path"] / "guideline-report.html")

            if errors:
                repos_fail += 1
                status_badge = '<span class="badge badge-error">FAIL</span>'
                status_icon = "&#128308;"
            else:
                repos_pass += 1
                status_badge = '<span class="badge badge-ok">PASS</span>'
                status_icon = "&#128994;"

            # Relative link from synthesis to per-repo report
            try:
                rel_report = report_path.relative_to(output_path.parent)
            except ValueError:
                rel_report = report_path

            # Collect top rules / files
            results = entry.get("results", [])
            for r in results:
                for v in r.violations:
                    rule_counter[r.instruction.description or r.instruction.path.stem] += 1
                    try:
                        rel = str(v.file.relative_to(entry["path"]))
                    except ValueError:
                        rel = str(v.file)
                    file_counter[f"{name}/{rel}"] += 1
            linter_results = entry.get("linter_results", [])
            for lr in linter_results:
                for v in lr.violations:
                    rule_counter[f"[{lr.linter}] {v.code}"] += 1
                    try:
                        rel = str(v.file.relative_to(entry["path"]))
                    except ValueError:
                        rel = str(v.file)
                    file_counter[f"{name}/{rel}"] += 1

            repo_rows_parts.append(
                f"<tr>"
                f"<td><strong>{_escape_html(name)}</strong></td>"
                f"<td>{status_badge}</td>"
                f"<td><a href='{_escape_html(str(rel_report))}' target='_blank'>&#128203; report</a></td>"
                f"<td style='text-align:right;color:var(--clr-err);font-weight:600'>"
                f"{errors if errors else '—'}</td>"
                f"<td style='text-align:right;color:var(--clr-warn)'>"
                f"{warnings if warnings else '—'}</td>"
                f"<td><span class='badge badge-ok'>&#10003; processed</span></td>"
                f"</tr>"
            )
            status_items_parts.append(
                f'<div class="status-item">'
                f'<span class="status-icon">{status_icon}</span>'
                f'<span class="status-label">'
                f'<a href="{_escape_html(str(rel_report))}" target="_blank">'
                f"{_escape_html(name)}</a></span>"
                f"{status_badge}"
                f"</div>"
            )

        # Top violated rules (top 15)
        top_rules = rule_counter.most_common(15)
        max_rule_count = top_rules[0][1] if top_rules else 1
        top_rules_rows_parts: list[str] = []
        for rule_name, count in top_rules:
            bar_pct = int(count / max_rule_count * 100)
            top_rules_rows_parts.append(
                f"<tr>"
                f"<td style='font-size:.78rem;max-width:240px;overflow:hidden"
                f";text-overflow:ellipsis;white-space:nowrap'"
                f" title='{_escape_html(rule_name)}'>{_escape_html(rule_name[:50])}</td>"
                f"<td style='text-align:right;font-weight:700;color:var(--clr-err)'>{count}</td>"
                f"<td style='width:80px'><div class='bar-bg'><div class='bar-fill err'"
                f" style='width:{bar_pct}%'></div></div></td>"
                f"</tr>"
            )

        # Top affected files (top 15)
        top_files = file_counter.most_common(15)
        max_file_count = top_files[0][1] if top_files else 1
        top_files_rows_parts: list[str] = []
        for file_name, count in top_files:
            bar_pct = int(count / max_file_count * 100)
            short = file_name.split("/")[-1]
            top_files_rows_parts.append(
                f"<tr>"
                f"<td style='font-size:.78rem' title='{_escape_html(file_name)}'>"
                f"<span style='color:var(--clr-muted)'>"
                f"{_escape_html('/'.join(file_name.split('/')[:2]))}/</span>"
                f"{_escape_html(short)}</td>"
                f"<td style='text-align:right;font-weight:700'>{count}</td>"
                f"<td style='width:80px'><div class='bar-bg'><div class='bar-fill'"
                f" style='width:{bar_pct}%'></div></div></td>"
                f"</tr>"
            )

        html = _SYNTHESIS_TEMPLATE.format(
            workspace=_escape_html(str(workspace)),
            generated_at=generated_at,
            total_repos=total_repos,
            repos_pass=repos_pass,
            repos_fail=repos_fail,
            repos_skip=repos_skip,
            total_errors=total_errors,
            total_warnings=total_warnings,
            repo_rows="\n".join(repo_rows_parts),
            status_items="\n".join(status_items_parts),
            top_rules_rows="".join(top_rules_rows_parts)
            or "<tr><td colspan='3' style='color:var(--clr-muted)'>No violations</td></tr>",
            top_files_rows="".join(top_files_rows_parts)
            or "<tr><td colspan='3' style='color:var(--clr-muted)'>No violations</td></tr>",
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
