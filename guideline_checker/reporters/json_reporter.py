"""JSON report generator for guideline-checker results (CI artifact).

The payload is a **contract** (ADR D-0022): a consumer — Standards Hub above all —
pins ``schema_version`` and reads documented fields, instead of pinning the tool's
git tag and coupling itself to every unrelated release.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from guideline_checker.baseline import fingerprint
from guideline_checker.core.detection import RuleResult, Violation, kind_of_detector, kind_of_phrase
from guideline_checker.core.health import RuleHealth, summarize
from guideline_checker.loader import InstructionFile

# The result contract's own version, independent of the tool's release version.
# Bump the minor for an additive field, the major for a removal or a changed
# meaning. SARIF keeps its own "2.1.0" — that version belongs to the SARIF spec.
SCHEMA_VERSION = "1.1"  # 1.1: additive "health" field (rule-health matrix, spec §4)


def _kind_of(instruction: InstructionFile, rule: str) -> str:
    """The mechanism this rule was measured by (ADR D-0020).

    A YAML rule is classified from its declarative detector; a markdown rule the
    checker recognised by prose is classified from that prose. Every rule reports
    exactly one kind, because a blank would make the field unusable to a consumer.
    """
    detector = instruction.rule_detectors.get(rule)
    return (kind_of_detector(detector) or kind_of_phrase(rule)).value


def _health_entry(entry: RuleHealth) -> dict[str, object]:
    """One rule's detection health, for a consumer that wants the matrix, not a green scan."""
    return {
        "rule": entry.rule,
        "instruction": entry.instruction,
        "state": entry.state.value,
        "has_declarative_detector": entry.has_declarative_detector,
        "has_phrase_detection": entry.has_phrase_detection,
        "fire_count": entry.fire_count,
        "reason": entry.reason,
        "provenance": entry.provenance,
        "kind": entry.kind,
    }


def _violation_entry(violation: Violation, instruction: InstructionFile, root: Path) -> dict[str, object]:
    """One violation, with the evidence a consumer needs to act on it.

    ``fingerprint`` is the same content hash the baseline uses, so a consumer can
    join a result to the project's accepted debt without re-deriving it — the
    difference between "a finding" and "a finding this project already accepts".
    """
    return {
        "severity": violation.severity,
        "file": str(violation.file.relative_to(root)) if violation.file.is_relative_to(root) else str(violation.file),
        "line": violation.line_number,
        "content": violation.line_content,
        "rule": violation.rule,
        "kind": _kind_of(instruction, violation.rule),
        "fingerprint": fingerprint(violation, root),
    }


class JsonReporter:
    """Generate a JSON compliance report."""

    def write(
        self,
        results: list[RuleResult],
        output_path: Path,
        root: Path,
        health: list[RuleHealth] | None = None,
    ) -> None:
        """Write the JSON report to output_path."""
        rules_list: list[dict[str, object]] = [
            {
                "instruction_file": str(result.instruction.path.name),
                "description": result.instruction.description,
                "apply_to": result.instruction.apply_to,
                "files_checked": result.files_checked,
                "violations": [_violation_entry(v, result.instruction, root) for v in result.violations],
            }
            for result in results
        ]

        report: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "project_root": str(root),
            "summary": {
                "files_checked": sum(r.files_checked for r in results),
                "total_violations": sum(len(r.violations) for r in results),
                "errors": sum(sum(1 for v in r.violations if v.severity == "error") for r in results),
                "warnings": sum(sum(1 for v in r.violations if v.severity == "warning") for r in results),
                "info": sum(sum(1 for v in r.violations if v.severity == "info") for r in results),
            },
            # Rule health leads the report — a consumer reads it before "rules"
            # (the violation list), per spec: rule-health is the headline.
            "health": {
                "summary": summarize(health) if health else None,
                "rules": [_health_entry(h) for h in health] if health else [],
            },
            "rules": rules_list,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
