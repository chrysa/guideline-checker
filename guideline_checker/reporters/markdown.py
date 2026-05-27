"""Markdown report generator for guideline-checker results."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from guideline_checker.checker import RuleResult

_SEVERITY_EMOJI = {
    "error": "🔴",
    "warning": "🟡",
    "info": "🔵",
}


class MarkdownReporter:
    """Generate a Markdown compliance report with audit overview and constraints."""

    def write(self, results: list[RuleResult], output_path: Path, root: Path) -> None:
        """Write the Markdown report to output_path."""
        generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        total_files = sum(r.files_checked for r in results)
        total_errors = sum(sum(1 for v in r.violations if v.severity == "error") for r in results)
        total_warnings = sum(sum(1 for v in r.violations if v.severity == "warning") for r in results)
        total_info = sum(sum(1 for v in r.violations if v.severity == "info") for r in results)
        total_violations = total_errors + total_warnings + total_info
        total_constraints = sum(len(r.instruction.rules) for r in results)

        lines: list[str] = [
            "# Guideline Compliance Report",
            "",
            f"**Project:** `{root}`  ",
            f"**Generated:** {generated_at}  ",
            "**Tool:** [guideline-checker](https://github.com/chrysa/guideline-checker)",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Files scanned | {total_files} |",
            f"| Rule files | {len(results)} |",
            f"| Constraints extracted | {total_constraints} |",
            f"| 🔴 Errors | {total_errors} |",
            f"| 🟡 Warnings | {total_warnings} |",
            f"| 🔵 Info | {total_info} |",
            f"| **Total violations** | **{total_violations}** |",
            "",
        ]

        # ── Audit overview table ──────────────────────────────────────────────
        lines += [
            "## 📊 Audit Overview",
            "",
            "| Guideline | Apply To | Files | Constraints | 🔴 Errors | 🟡 Warnings | Status |",
            "|-----------|----------|------:|------------:|----------:|------------:|--------|",
        ]
        for r in results:
            title = (r.instruction.description or r.instruction.path.stem).replace("|", "\\|")
            apply_to = r.instruction.apply_to.replace("|", "\\|")
            n_rules = len(r.instruction.rules)
            n_err = sum(1 for v in r.violations if v.severity == "error")
            n_warn = sum(1 for v in r.violations if v.severity == "warning")
            status = "❌ FAIL" if n_err else ("⚠️ WARN" if n_warn else "✅ PASS")
            lines.append(f"| {title} | `{apply_to}` | {r.files_checked} | {n_rules} | {n_err} | {n_warn} | {status} |")
        lines.append("")

        # ── Per-rule sections ─────────────────────────────────────────────────
        lines += ["## 📋 Details by Guideline", ""]
        for result in results:
            title = result.instruction.description or result.instruction.path.stem
            n_err = sum(1 for v in result.violations if v.severity == "error")
            n_warn = sum(1 for v in result.violations if v.severity == "warning")
            status_icon = "❌" if n_err else ("⚠️" if n_warn else "✅")

            lines += [
                f"### {status_icon} `{result.instruction.path.name}`",
                "",
                f"**Description:** {title}  ",
                f"**Source:** {result.instruction.source_type}  ",
                f"**Applies to:** `{result.instruction.apply_to}`  ",
                f"**Files scanned:** {result.files_checked}  ",
                f"**Constraints extracted:** {len(result.instruction.rules)}  ",
                "",
            ]

            # Constraints list
            if result.instruction.rules:
                lines += ["<details>", "<summary>📜 Extracted constraints</summary>", ""]
                for i, rule in enumerate(result.instruction.rules, 1):
                    lines.append(f"{i}. {rule}")
                lines += ["", "</details>", ""]
            else:
                lines += ["> No constraints extracted from this file.", ""]

            # Violations grouped by file
            if not result.violations:
                lines += ["✅ **No violations found.**", ""]
                continue

            lines += [f"**Violations: {len(result.violations)}** ({n_err} error(s), {n_warn} warning(s))", ""]

            by_file: dict[Path, list[object]] = defaultdict(list)
            for v in result.violations:
                by_file[v.file].append(v)

            for fpath, fviolations in sorted(by_file.items()):
                try:
                    rel_path = str(fpath.relative_to(root))
                except ValueError:
                    rel_path = str(fpath)

                lines += [f"#### 📄 `{rel_path}`", ""]
                lines += [
                    "| Severity | Line | Code | Rule |",
                    "|----------|-----:|------|------|",
                ]
                for v in sorted(fviolations, key=lambda x: x.line_number):
                    emoji = _SEVERITY_EMOJI.get(v.severity, "⚪")
                    content_esc = v.line_content.replace("|", "\\|").replace("`", "'")
                    rule_esc = v.rule.replace("|", "\\|")[:100]
                    lines.append(f"| {emoji} `{v.severity}` | {v.line_number} | `{content_esc}` | {rule_esc} |")
                lines.append("")

        lines += [
            "---",
            "",
            "*Generated by [guideline-checker](https://github.com/chrysa/guideline-checker)*",
        ]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
