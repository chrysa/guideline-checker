"""HTML report generator for guideline-checker results."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

from guideline_checker.checker import RuleResult, Violation

if TYPE_CHECKING:
    from guideline_checker.linters import LinterResult

# ─── Templates ────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Guideline Compliance Report</title>
<style>
:root {{
  --clr-bg: #f8f9fa; --clr-surface: #fff; --clr-border: #dee2e6;
  --clr-muted: #6c757d; --clr-dark: #343a40; --clr-text: #212529;
  --clr-ok: #198754; --clr-err: #dc3545; --clr-warn: #fd7e14; --clr-info: #0d6efd;
  --nav-width: 280px;
}}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ font-family: system-ui, sans-serif; margin: 0; background: var(--clr-bg);
  color: var(--clr-text); display: flex; flex-direction: column; min-height: 100vh; }}
a {{ color: var(--clr-info); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* ── Header ── */
header {{ background: var(--clr-dark); color: #fff; padding: 1rem 1.5rem;
  display: flex; align-items: center; gap: 1rem; position: sticky; top: 0; z-index: 100;
  box-shadow: 0 2px 6px rgba(0,0,0,.3); }}
header h1 {{ margin: 0; font-size: 1.2rem; flex: 1; }}
header p {{ margin: 0; opacity: .7; font-size: 0.8rem; white-space: nowrap; }}
#nav-toggle {{ background: none; border: 1px solid rgba(255,255,255,.4); color: #fff;
  padding: .3rem .7rem; border-radius: 4px; cursor: pointer; font-size: .85rem; }}

/* ── Layout ── */
.layout {{ display: flex; flex: 1; }}

/* ── Sidebar / Nav ── */
nav#sidebar {{
  width: var(--nav-width); min-width: var(--nav-width);
  background: #fff; border-right: 1px solid var(--clr-border);
  position: sticky; top: 53px; height: calc(100vh - 53px);
  overflow-y: auto; padding: .5rem 0; flex-shrink: 0;
  transition: margin-left .2s ease;
}}
nav#sidebar.hidden {{ margin-left: calc(-1 * var(--nav-width)); }}
.nav-section {{ padding: .25rem 0; }}
.nav-section-header {{ display: flex; align-items: center; gap: .4rem;
  padding: .35rem .75rem; font-size: .78rem; font-weight: 600;
  color: var(--clr-muted); text-transform: uppercase; letter-spacing: .05em; }}
.nav-item {{ display: flex; align-items: center; gap: .4rem;
  padding: .3rem .75rem .3rem 1rem; font-size: .8rem; cursor: pointer;
  border-left: 3px solid transparent; transition: background .15s; }}
.nav-item:hover {{ background: #f0f4ff; }}
.nav-item.active {{ background: #e8f0fe; border-left-color: var(--clr-info); }}
.nav-item.err {{ border-left-color: var(--clr-err); }}
.nav-item.warn {{ border-left-color: var(--clr-warn); }}
.nav-item.ok {{ border-left-color: var(--clr-ok); }}
.nav-item .nav-label {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.nav-item .nav-count {{ font-size: .7rem; padding: .1em .45em; border-radius: 10px;
  background: #e9ecef; color: var(--clr-muted); flex-shrink: 0; }}
.nav-item.err .nav-count {{ background: #f8d7da; color: #842029; }}
.nav-item.warn .nav-count {{ background: #fff3cd; color: #664d03; }}
.nav-sub {{ padding-left: 1rem; }}
.nav-sub .nav-item {{ font-size: .75rem; padding-left: 1.25rem; }}

/* ── Main content ── */
main {{ flex: 1; padding: 1rem 1.5rem; min-width: 0; }}

/* ── Summary bar ── */
.summary {{ display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 1.25rem; }}
.stat {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 8px; padding: .75rem 1.25rem; min-width: 110px; text-align: center; }}
.stat .value {{ font-size: 1.8rem; font-weight: 700; }}
.stat .label {{ font-size: .75rem; color: var(--clr-muted); text-transform: uppercase;
  letter-spacing: .05em; }}
.stat.error .value {{ color: var(--clr-err); }}
.stat.warning .value {{ color: var(--clr-warn); }}
.stat.info .value {{ color: var(--clr-info); }}
.stat.ok .value {{ color: var(--clr-ok); }}

/* ── Audit overview table ── */
.audit-table-wrap {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 8px; overflow: hidden; margin-bottom: 1.5rem; }}
.audit-table-title {{ background: #e9ecef; padding: .6rem 1rem; font-weight: 600;
  font-size: .9rem; display: flex; align-items: center; gap: .5rem; }}

/* ── Sections (per rule) ── */
.section {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 8px; overflow: hidden; margin-bottom: 1rem; scroll-margin-top: 60px; }}
.section-header {{ padding: .7rem 1rem; background: #e9ecef;
  display: flex; justify-content: space-between; align-items: flex-start; gap: .5rem; }}
.section-header-left h2 {{ margin: 0; font-size: .95rem; }}
.section-meta {{ font-size: .75rem; color: var(--clr-muted); margin-top: .2rem; }}
.section-meta code {{ background: #dee2e6; padding: .1em .3em; border-radius: 3px; }}

/* ── Badges ── */
.badge {{ display: inline-block; padding: .2em .55em; border-radius: 4px;
  font-size: .72rem; font-weight: 700; white-space: nowrap; }}
.badge-error {{ background: #f8d7da; color: #842029; }}
.badge-warning {{ background: #fff3cd; color: #664d03; }}
.badge-info {{ background: #cfe2ff; color: #084298; }}
.badge-ok {{ background: #d1e7dd; color: #0a3622; }}
.badge-neutral {{ background: #e9ecef; color: #495057; }}

/* ── Constraints details ── */
details.constraints {{ margin: 0; }}
details.constraints summary {{
  padding: .5rem 1rem; cursor: pointer; font-size: .82rem; color: var(--clr-muted);
  background: #f8f9fa; border-top: 1px solid var(--clr-border);
  list-style: none; display: flex; align-items: center; gap: .4rem;
  user-select: none;
}}
details.constraints summary::-webkit-details-marker {{ display: none; }}
details.constraints summary::before {{ content: "▶"; font-size: .6rem; transition: transform .15s; }}
details[open].constraints summary::before {{ transform: rotate(90deg); }}
details.constraints summary:hover {{ background: #e9ecef; }}
.constraints-body {{ padding: .5rem 1rem .75rem; border-top: 1px solid var(--clr-border);
  background: #fafbfc; }}
.constraints-body ol {{ margin: 0; padding-left: 1.25rem; font-size: .82rem; line-height: 1.7; }}
.constraints-body li {{ color: var(--clr-text); }}
.constraints-body li .kw {{ font-weight: 600; color: #6f42c1; }}

/* ── File groups in violations ── */
.file-group {{ border-top: 1px solid var(--clr-border); }}
.file-group-header {{ padding: .4rem 1rem; background: #f8f9fa; font-family: monospace;
  font-size: .78rem; color: var(--clr-muted); display: flex; align-items: center;
  gap: .5rem; }}
.file-group-header .file-icon {{ opacity: .5; }}

/* ── Tables ── */
table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
th {{ background: #f8f9fa; padding: .45rem .9rem; text-align: left;
  border-bottom: 2px solid var(--clr-border); white-space: nowrap; }}
td {{ padding: .4rem .9rem; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: #f8f9fa; }}
.file-path {{ font-family: monospace; font-size: .78rem; color: var(--clr-muted); }}
.line-content {{ font-family: monospace; background: #f8f9fa; padding: .1em .4em;
  border-radius: 3px; font-size: .8rem; }}
.rule-text {{ max-width: 380px; font-size: .8rem; color: #495057; }}
td.line-no {{ width: 50px; text-align: right; color: var(--clr-muted);
  font-family: monospace; font-size: .78rem; }}

/* ── No violations ── */
.no-violations {{ padding: .9rem 1rem; color: var(--clr-ok); font-size: .875rem; }}

/* ── Linter sections ── */
.linter-section {{ background: var(--clr-surface); border: 1px solid var(--clr-border);
  border-radius: 8px; overflow: hidden; margin-bottom: 1rem; scroll-margin-top: 60px; }}
.linter-header {{ padding: .7rem 1rem; background: #e3f2fd;
  display: flex; justify-content: space-between; align-items: center; gap: .5rem; }}
.linter-header h2 {{ margin: 0; font-size: .95rem; }}
.linter-meta {{ font-size: .75rem; color: var(--clr-muted); }}
.linter-unavailable {{ padding: .9rem 1rem; color: var(--clr-muted); font-size: .875rem; font-style: italic; }}
.linter-ok {{ padding: .9rem 1rem; color: var(--clr-ok); font-size: .875rem; }}

/* ── Footer ── */
footer {{ text-align: center; color: var(--clr-muted); font-size: .78rem;
  padding: 1.5rem; border-top: 1px solid var(--clr-border); margin-top: 1rem; }}
</style>
</head>
<body>
<header>
  <button id="nav-toggle"
    onclick="document.getElementById('sidebar').classList.toggle('hidden')"
    title="Toggle navigation">&#9776;</button>
  <h1>&#128203; Guideline Compliance Report</h1>
  <p>&#128193; {project_root} &nbsp;&mdash;&nbsp; &#128197; {generated_at}</p>
</header>
<div class="layout">
<nav id="sidebar">
  <div class="nav-section">
    <div class="nav-section-header">&#128270; Navigation</div>
    <div class="nav-item" onclick="document.getElementById('audit-overview').scrollIntoView({{behavior:'smooth'}})">
      <span class="nav-label">&#128202; Audit Overview</span>
    </div>
  </div>
  <div class="nav-section">
    <div class="nav-section-header">&#128218; Guidelines ({total_rules})</div>
    {nav_items}
  </div>
  {linter_nav}
</nav>
<main>
<div class="summary">
  <div class="stat ok"><div class="value">{total_files}</div><div class="label">Files scanned</div></div>
  <div class="stat error"><div class="value">{total_errors}</div><div class="label">Errors</div></div>
  <div class="stat warning"><div class="value">{total_warnings}</div><div class="label">Warnings</div></div>
  <div class="stat info"><div class="value">{total_info}</div><div class="label">Info</div></div>
  <div class="stat"><div class="value">{total_rules}</div><div class="label">Rule files</div></div>
  <div class="stat"><div class="value">{total_constraints}</div><div class="label">Constraints</div></div>
  {linter_stats}
</div>

<div class="audit-table-wrap" id="audit-overview">
  <div class="audit-table-title">&#128202; Audit Overview — Compliance by Guideline</div>
  <table>
    <thead>
      <tr>
        <th>Guideline</th><th>Apply To</th><th>Files</th>
        <th>Constraints</th><th>&#128308; Errors</th><th>&#128993; Warnings</th><th>Status</th>
      </tr>
    </thead>
    <tbody>
      {audit_rows}
    </tbody>
  </table>
</div>

{sections}
</main>
</div>
<footer>Generated by <a href="https://github.com/chrysa/guideline-checker">guideline-checker</a></footer>
<script>
// Highlight active nav item on scroll
const sections = document.querySelectorAll('.section[id]');
const navItems = document.querySelectorAll('.nav-item[data-target]');
const observer = new IntersectionObserver(entries => {{
  entries.forEach(e => {{
    if (e.isIntersecting) {{
      navItems.forEach(n => n.classList.remove('active'));
      const active = document.querySelector('.nav-item[data-target="' + e.target.id + '"]');
      if (active) {{ active.classList.add('active'); active.scrollIntoView({{block:'nearest'}}); }}
    }}
  }});
}}, {{rootMargin: '-10% 0px -80% 0px'}});
sections.forEach(s => observer.observe(s));
</script>
</body>
</html>
"""


# ─── Reporter ──────────────────────────────────────────────────────────────────


class HtmlReporter:
    """Generate an HTML compliance report with sidebar navigation and audit overview."""

    def write(
        self,
        results: list[RuleResult],
        output_path: Path,
        root: Path,
        linter_results: list[LinterResult] | None = None,
    ) -> None:
        """Write the HTML report to *output_path*."""
        from datetime import datetime

        generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        total_files = sum(r.files_checked for r in results)
        total_errors = sum(sum(1 for v in r.violations if v.severity == "error") for r in results)
        total_warnings = sum(sum(1 for v in r.violations if v.severity == "warning") for r in results)
        total_info = sum(sum(1 for v in r.violations if v.severity == "info") for r in results)
        total_constraints = sum(len(r.instruction.rules) for r in results)

        # Linter stats
        linter_results = linter_results or []
        linter_errors = sum(sum(1 for v in lr.violations if v.severity == "error") for lr in linter_results)
        linter_warnings = sum(sum(1 for v in lr.violations if v.severity == "warning") for lr in linter_results)

        nav_items_html = self._render_nav_items(results, root)
        linter_nav_html = self._render_linter_nav(linter_results)
        audit_rows_html = self._render_audit_rows(results, root)
        sections_html = "".join(self._render_section(r, root, idx) for idx, r in enumerate(results))

        if linter_results:
            sections_html += self._render_linter_section(linter_results, root)

        linter_stats_html = ""
        if linter_results:
            linter_stats_html = (
                f'<div class="stat error"><div class="value">{linter_errors}</div>'
                f'<div class="label">Linter errors</div></div>'
                f'<div class="stat warning"><div class="value">{linter_warnings}</div>'
                f'<div class="label">Linter warnings</div></div>'
            )

        html = _HTML_TEMPLATE.format(
            project_root=_escape_html(str(root)),
            generated_at=generated_at,
            total_files=total_files,
            total_errors=total_errors,
            total_warnings=total_warnings,
            total_info=total_info,
            total_rules=len(results),
            total_constraints=total_constraints,
            nav_items=nav_items_html,
            linter_nav=linter_nav_html,
            audit_rows=audit_rows_html,
            sections=sections_html,
            linter_stats=linter_stats_html,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    # ── Linter sections ────────────────────────────────────────────────────────

    def _render_linter_nav(self, linter_results: list[object] | None) -> str:
        """Render sidebar nav section for linters."""
        if not linter_results:
            return ""
        items: list[str] = []
        for lr in linter_results:
            lname = lr.linter
            lid = f"linter-{lname}"
            if not lr.available:
                cls, count_html = "warn", '<span class="nav-count">N/A</span>'
            else:
                n_err = sum(1 for v in lr.violations if v.severity == "error")
                n_warn = sum(1 for v in lr.violations if v.severity == "warning")
                if n_err:
                    cls = "err"
                    count_html = f'<span class="nav-count">{n_err} err</span>'
                elif n_warn:
                    cls = "warn"
                    count_html = f'<span class="nav-count">{n_warn} warn</span>'
                else:
                    cls = "ok"
                    count_html = '<span class="nav-count">&#10003;</span>'
            items.append(
                f'<div class="nav-item {cls}" '
                f"onclick=\"document.getElementById('{lid}').scrollIntoView({{behavior:'smooth'}})\">"
                f'<span class="nav-label">&#128295; {_escape_html(lname)}</span>'
                f"{count_html}"
                f"</div>"
            )
        inner = "\n".join(items)
        return (
            f'<div class="nav-section">'
            f'<div class="nav-section-header">&#128295; Linters ({len(linter_results)})</div>'
            f"{inner}"
            f"</div>"
        )

    def _render_linter_section(self, linter_results: list[object] | None, root: Path) -> str:
        """Render the full linter results section for all linters."""
        if not linter_results:
            return ""
        parts: list[str] = []
        for lr in linter_results:
            lid = f"linter-{lr.linter}"
            n_err = sum(1 for v in lr.violations if v.severity == "error")
            n_warn = sum(1 for v in lr.violations if v.severity == "warning")
            total = n_err + n_warn

            if not lr.available:
                badge = '<span class="badge badge-neutral">UNAVAILABLE</span>'
                body = (
                    f'<div class="linter-unavailable">&#128683; Linter not found: {_escape_html(lr.error or "")}</div>'
                )
            elif lr.error:
                badge = '<span class="badge badge-warning">ERROR</span>'
                body = f'<div class="linter-unavailable">&#9888; {_escape_html(lr.error)}</div>'
            elif total == 0:
                badge = '<span class="badge badge-ok">PASS</span>'
                body = '<div class="linter-ok">&#10003; No violations found</div>'
            else:
                if n_err:
                    badge = f'<span class="badge badge-error">{n_err} errors</span>'
                else:
                    badge = f'<span class="badge badge-warning">{n_warn} warnings</span>'
                body = self._render_linter_violations(lr.violations, root)

            parts.append(
                f'<div class="linter-section" id="{lid}">'
                f'<div class="linter-header">'
                f"<h2>&#128295; {_escape_html(lr.linter)}</h2>"
                f'<span class="linter-meta">{total} violation(s)</span>'
                f"{badge}"
                f"</div>"
                f"{body}"
                f"</div>"
            )
        return "\n".join(parts)

    def _render_linter_violations(self, violations: list[object], root: Path) -> str:
        """Render linter violations grouped by file."""
        by_file: dict[Path, list[object]] = defaultdict(list)
        for v in violations:
            by_file[v.file].append(v)

        parts: list[str] = []
        for fpath, fviolations in sorted(by_file.items()):
            try:
                rel_path = str(fpath.relative_to(root))
            except ValueError:
                rel_path = str(fpath)

            n_err = sum(1 for v in fviolations if v.severity == "error")
            n_warn = sum(1 for v in fviolations if v.severity == "warning")
            file_badge = (
                f'<span class="badge badge-error">{n_err} err</span>'
                if n_err
                else f'<span class="badge badge-warning">{n_warn} warn</span>'
            )
            rows = []
            for v in sorted(fviolations, key=lambda x: x.line):
                sev_class = "badge-error" if v.severity == "error" else "badge-warning"
                code_html = f'<code style="font-size:.78rem">{_escape_html(v.code)}</code>' if v.code else ""
                rows.append(
                    f"<tr>"
                    f"<td><span class='badge {sev_class}'>{v.severity.upper()}</span></td>"
                    f"<td class='line-no'>{v.line}</td>"
                    f"<td class='line-no'>{v.col}</td>"
                    f"<td>{code_html}</td>"
                    f"<td class='rule-text'>{_escape_html(v.message[:200])}</td>"
                    f"</tr>"
                )
            parts.append(
                f'<div class="file-group">'
                f'<div class="file-group-header">'
                f'<span class="file-icon">&#128196;</span>'
                f'<span style="flex:1;font-weight:600">{_escape_html(rel_path)}</span>'
                f"{file_badge}"
                f"</div>"
                f"<table>"
                f"<thead><tr><th>Sev</th><th>Line</th><th>Col</th><th>Code</th><th>Message</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody>"
                f"</table>"
                f"</div>"
            )
        return "".join(parts)

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _render_nav_items(
        self,
        results: list[RuleResult],
        root: Path,
        linter_results: list[LinterResult] | None = None,
    ) -> str:
        items: list[str] = []
        for idx, r in enumerate(results):
            section_id = f"rule-{idx}"
            title = _short_title(r.instruction.description or r.instruction.path.stem)
            n_err = sum(1 for v in r.violations if v.severity == "error")
            n_warn = sum(1 for v in r.violations if v.severity == "warning")
            n_viol = len(r.violations)

            if n_err:
                cls = "err"
                count_html = f'<span class="nav-count">{n_err} err</span>'
            elif n_warn:
                cls = "warn"
                count_html = f'<span class="nav-count">{n_warn} warn</span>'
            else:
                cls = "ok"
                count_html = '<span class="nav-count">✓</span>'

            items.append(
                f'<div class="nav-item {cls}" data-target="{section_id}" '
                f"onclick=\"document.getElementById('{section_id}').scrollIntoView({{behavior:'smooth'}})\">"
                f'<span class="nav-label" title="{_escape_html(title)}">{_escape_html(title)}</span>'
                f"{count_html}"
                f"</div>"
            )

            # Sub-items: affected files (max 8 shown)
            if n_viol > 0:
                files_seen: dict[str, int] = {}
                for v in r.violations:
                    try:
                        rel = str(v.file.relative_to(root))
                    except ValueError:
                        rel = str(v.file)
                    files_seen[rel] = files_seen.get(rel, 0) + 1

                sub_items: list[str] = []
                for i, (fpath, cnt) in enumerate(list(files_seen.items())[:8]):
                    fname = fpath.split("/")[-1]
                    file_id = f"file-{idx}-{i}"
                    sub_items.append(
                        f'<div class="nav-item" style="padding-left:1.75rem;font-size:.73rem" '
                        f"onclick=\"document.getElementById('{file_id}').scrollIntoView({{behavior:'smooth'}})\">"
                        f'<span class="nav-label" title="{_escape_html(fpath)}">&#128196; {_escape_html(fname)}</span>'
                        f'<span class="nav-count">{cnt}</span>'
                        f"</div>"
                    )
                if len(files_seen) > 8:
                    sub_items.append(
                        f'<div class="nav-item" style="padding-left:1.75rem;font-size:.73rem;color:var(--clr-muted)">'
                        f'<span class="nav-label">…{len(files_seen) - 8} more files</span>'
                        f"</div>"
                    )
                items.append(f'<div class="nav-sub">{"".join(sub_items)}</div>')

        # Add linter nav section if linters ran
        if linter_results:
            total_linter_errors = sum(sum(1 for v in lr.violations if v.severity == "error") for lr in linter_results)
            linter_cls = "err" if total_linter_errors else "ok"
            items.append('<div class="nav-section-header" style="margin-top:.5rem">&#128270; Linters</div>')
            items.append(
                f'<div class="nav-item {linter_cls}" data-target="linter-section" '
                f"onclick=\"document.getElementById('linter-section').scrollIntoView({{behavior:'smooth'}})\">"
                f'<span class="nav-label">&#128200; Linter results</span>'
                f'<span class="nav-count">{total_linter_errors} err</span>'
                f"</div>"
            )

        return "\n".join(items)

    # ── Audit overview ─────────────────────────────────────────────────────────

    def _render_audit_rows(self, results: list[RuleResult], root: Path) -> str:
        rows: list[str] = []
        for r in results:
            title = _escape_html(r.instruction.description or r.instruction.path.stem)
            apply_to = _escape_html(r.instruction.apply_to)
            n_rules = len(r.instruction.rules)
            n_err = sum(1 for v in r.violations if v.severity == "error")
            n_warn = sum(1 for v in r.violations if v.severity == "warning")

            if n_err:
                status = '<span class="badge badge-error">FAIL</span>'
            elif n_warn:
                status = '<span class="badge badge-warning">WARN</span>'
            else:
                status = '<span class="badge badge-ok">PASS</span>'

            err_cell = f'<span style="color:var(--clr-err);font-weight:600">{n_err}</span>' if n_err else "0"
            warn_cell = f'<span style="color:var(--clr-warn);font-weight:600">{n_warn}</span>' if n_warn else "0"

            rows.append(
                f"<tr>"
                f"<td>{title}</td>"
                f"<td><code style='font-size:.75rem'>{apply_to}</code></td>"
                f"<td style='text-align:right'>{r.files_checked}</td>"
                f"<td style='text-align:right'>{n_rules}</td>"
                f"<td style='text-align:right'>{err_cell}</td>"
                f"<td style='text-align:right'>{warn_cell}</td>"
                f"<td>{status}</td>"
                f"</tr>"
            )
        return "\n".join(rows)

    # ── Per-rule sections ──────────────────────────────────────────────────────

    def _render_section(self, result: RuleResult, root: Path, idx: int) -> str:
        """Render one rule-file section with constraints + grouped violations."""
        section_id = f"rule-{idx}"
        title = _escape_html(result.instruction.description or result.instruction.path.stem)
        apply_to = _escape_html(result.instruction.apply_to)
        n_err = sum(1 for v in result.violations if v.severity == "error")
        n_warn = sum(1 for v in result.violations if v.severity == "warning")

        if n_err:
            badge = '<span class="badge badge-error">FAIL</span>'
        elif n_warn:
            badge = f'<span class="badge badge-warning">{n_warn} warning(s)</span>'
        else:
            badge = '<span class="badge badge-ok">PASS</span>'

        source_badge = (
            f'<span class="badge badge-neutral" style="font-size:.68rem">'
            f"{_escape_html(str(result.instruction.source_type))}</span>"
        )

        # Build constraints block
        constraints_html = self._render_constraints(result.instruction.rules)

        # Build violations block
        if not result.violations:
            violations_html = '<div class="no-violations">&#10003; No violations found.</div>'
        else:
            violations_html = self._render_violations_by_file(result.violations, result.instruction.rules, root, idx)

        return f"""\
<div class="section" id="{section_id}">
  <div class="section-header">
    <div class="section-header-left">
      <h2>{title} {source_badge}</h2>
      <div class="section-meta">
        applyTo: <code>{apply_to}</code> &nbsp;&mdash;&nbsp;
        {result.files_checked} file(s) scanned &nbsp;&mdash;&nbsp;
        {len(result.instruction.rules)} constraint(s) extracted
      </div>
    </div>
    {badge}
  </div>
  {constraints_html}
  {violations_html}
</div>
"""

    def _render_constraints(self, rules: list[str]) -> str:
        """Render extracted constraints as a collapsible <details> block."""
        if not rules:
            return (
                '<details class="constraints">'
                "<summary>&#128683; No constraints extracted from this file</summary>"
                "</details>"
            )

        _kw_re = re.compile(
            r"\b(must|never|always|forbidden|required|non-negotiable|mandatory|do not|don\'t)\b",
            re.IGNORECASE,
        )

        def _highlight(text: str) -> str:
            return _kw_re.sub(
                lambda m: f'<span class="kw">{_escape_html(m.group())}</span>',
                _escape_html(text),
            )

        items = "".join(f"<li>{_highlight(r)}</li>" for r in rules)
        return (
            f'<details class="constraints">'
            f"<summary>&#128221; {len(rules)} constraint(s) extracted — click to expand</summary>"
            f'<div class="constraints-body"><ol>{items}</ol></div>'
            f"</details>"
        )

    def _render_violations_by_file(
        self,
        violations: list[Violation],
        rules: list[str],
        root: Path,
        section_idx: int,
    ) -> str:
        """Render violations grouped by file, with per-file anchors for nav."""
        # Group violations by file
        by_file: dict[Path, list[Violation]] = defaultdict(list)
        for v in violations:
            by_file[v.file].append(v)

        parts: list[str] = []
        for file_idx, (fpath, fviolations) in enumerate(sorted(by_file.items())):
            file_id = f"file-{section_idx}-{file_idx}"
            try:
                rel_path = str(fpath.relative_to(root))
            except ValueError:
                rel_path = str(fpath)

            n_err = sum(1 for v in fviolations if v.severity == "error")
            n_warn = sum(1 for v in fviolations if v.severity == "warning")
            file_badge = (
                f'<span class="badge badge-error">{n_err} err</span>'
                if n_err
                else f'<span class="badge badge-warning">{n_warn} warn</span>'
            )

            rows = []
            for v in sorted(fviolations, key=lambda x: x.line_number):
                sev_class = f"badge-{v.severity}"
                rows.append(
                    f"<tr>"
                    f"<td><span class='badge {sev_class}'>{v.severity.upper()}</span></td>"
                    f"<td class='line-no'>{v.line_number}</td>"
                    f"<td class='line-content'>{_escape_html(v.line_content)}</td>"
                    f"<td class='rule-text'>{_escape_html(v.rule[:160])}</td>"
                    f"</tr>"
                )
            rows_html = "\n".join(rows)

            parts.append(
                f'<div class="file-group" id="{file_id}">'
                f'<div class="file-group-header">'
                f'<span class="file-icon">&#128196;</span>'
                f'<span style="flex:1;font-weight:600">{_escape_html(rel_path)}</span>'
                f"{file_badge}"
                f"</div>"
                f"<table>"
                f"<thead><tr><th>Severity</th><th>Line</th><th>Code</th><th>Rule</th></tr></thead>"
                f"<tbody>{rows_html}</tbody>"
                f"</table>"
                f"</div>"
            )

        return "".join(parts)


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _escape_html(text: str) -> str:
    """Escape special HTML characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _short_title(title: str, max_len: int = 36) -> str:
    """Truncate long titles for nav display."""
    return title if len(title) <= max_len else title[: max_len - 1] + "…"
