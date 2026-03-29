"""JSON report generator for guideline-checker results (CI artifact)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from guideline_checker.checker import RuleResult


class JsonReporter:
    """Generate a JSON compliance report."""

    def write(self, results: list[RuleResult], output_path: Path, root: Path) -> None:
        """Write the JSON report to output_path."""
        report = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "project_root": str(root),
            "summary": {
                "files_checked": sum(r.files_checked for r in results),
                "total_violations": sum(len(r.violations) for r in results),
                "errors": sum(sum(1 for v in r.violations if v.severity == "error") for r in results),
                "warnings": sum(sum(1 for v in r.violations if v.severity == "warning") for r in results),
                "info": sum(sum(1 for v in r.violations if v.severity == "info") for r in results),
            },
            "rules": [],
        }

        for result in results:
            rule_entry = {
                "instruction_file": str(result.instruction.path.name),
                "description": result.instruction.description,
                "apply_to": result.instruction.apply_to,
                "files_checked": result.files_checked,
                "violations": [
                    {
                        "severity": v.severity,
                        "file": str(v.file.relative_to(root)) if v.file.is_relative_to(root) else str(v.file),
                        "line": v.line_number,
                        "content": v.line_content,
                        "rule": v.rule,
                    }
                    for v in result.violations
                ],
            }
            report["rules"].append(rule_entry)  # type: ignore[union-attr]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
