"""SARIF 2.1.0 report generator for guideline-checker.

SARIF (Static Analysis Results Interchange Format) is the industry standard
for static analysis tool results. GitHub Code Scanning accepts SARIF natively.

Spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from guideline_checker.core.detection import RuleResult
from guideline_checker.core.health import RuleHealth, summarize

_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"

_SEVERITY_MAP = {
    "error": "error",
    "warning": "warning",
    "info": "note",
}


class SarifReporter:
    """Generate a SARIF 2.1.0 compliance report (GitHub Code Scanning compatible)."""

    def write(
        self,
        results: list[RuleResult],
        output_path: Path,
        root: Path,
        health: list[RuleHealth] | None = None,
    ) -> None:
        """Write the SARIF report to output_path."""
        rules = self._build_rules(results)
        rule_index: dict[str, int] = {str(r["id"]): i for i, r in enumerate(rules)}
        run: dict[str, object] = {
            "tool": {
                "driver": {
                    "name": "guideline-checker",
                    "version": self._get_version(),
                    "informationUri": "https://github.com/chrysa/guideline-checker",
                    "rules": rules,
                }
            },
            "originalUriBaseIds": {"SRCROOT": {"uri": root.as_uri() + "/"}},
            "results": self._build_results(results, root, rule_index),
            "invocations": [
                {
                    "executionSuccessful": True,
                    "endTimeUtc": datetime.now(tz=UTC).isoformat(),
                }
            ],
        }
        if health:
            # SARIF has no first-class slot for detection-capability data; the spec's
            # "properties" bag is the documented extension point (§3.8). Rule health
            # leads the report elsewhere (HTML/Markdown/JSON) — carried here too, for
            # a SARIF-only consumer, rather than silently dropped.
            run["properties"] = {"ruleHealth": summarize(health)}
        runs = [run]

        sarif: dict[str, object] = {
            "$schema": _SARIF_SCHEMA,
            "version": _SARIF_VERSION,
            "runs": runs,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(sarif, indent=2, ensure_ascii=False), encoding="utf-8")

    def _get_version(self) -> str:
        try:
            from guideline_checker import __version__

            return __version__
        except ImportError:
            return "0.0.0"

    def _build_rules(self, results: list[RuleResult]) -> list[dict[str, object]]:
        """Build SARIF rule descriptors from instruction files."""
        seen: set[str] = set()
        rules: list[dict[str, object]] = []
        for result in results:
            rule_id = _sanitize_rule_id(result.instruction.path.stem)
            if rule_id in seen:
                continue
            seen.add(rule_id)
            rules.append(
                {
                    "id": rule_id,
                    "name": result.instruction.description,
                    "shortDescription": {"text": result.instruction.description},
                    "fullDescription": {"text": f"Checks applied to: {result.instruction.apply_to}"},
                    "helpUri": "https://github.com/chrysa/guideline-checker",
                    "properties": {
                        "tags": ["compliance", "guidelines"],
                        "applyTo": result.instruction.apply_to,
                    },
                }
            )
        return rules

    def _build_results(
        self,
        results: list[RuleResult],
        root: Path,
        rule_index: dict[str, int],
    ) -> list[dict[str, object]]:
        """Build SARIF result entries from violations."""
        sarif_results: list[dict[str, object]] = []
        for result in results:
            rule_id = _sanitize_rule_id(result.instruction.path.stem)
            idx = rule_index.get(rule_id, 0)
            for violation in result.violations:
                rel_path = (
                    str(violation.file.relative_to(root))
                    if violation.file.is_relative_to(root)
                    else str(violation.file)
                )
                sarif_results.append(
                    {
                        "ruleId": rule_id,
                        "ruleIndex": idx,
                        "level": _SEVERITY_MAP.get(violation.severity, "warning"),
                        "message": {"text": violation.rule},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": rel_path,
                                        "uriBaseId": "SRCROOT",
                                    },
                                    "region": {
                                        "startLine": violation.line_number,
                                        "snippet": {"text": violation.line_content},
                                    },
                                }
                            }
                        ],
                    }
                )
        return sarif_results


def _sanitize_rule_id(stem: str) -> str:
    """Convert an instruction file stem to a valid SARIF rule ID."""
    return re.sub(r"[^a-zA-Z0-9._/-]", "-", stem)
