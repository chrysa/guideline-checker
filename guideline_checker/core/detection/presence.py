"""Whole-file presence/absence checks: a rule's declarative detector fan-out,
mandatory-content requirements, and file-freshness (ADR D-0020)."""

from __future__ import annotations

import re
import time
from pathlib import Path

from guideline_checker.core.detection import Violation, _ast_violations, _scan_violations
from guideline_checker.core.detection.crossref import _cross_reference_violations
from guideline_checker.core.detection.numeric import _check_function_lengths, _numeric_threshold_violations
from guideline_checker.core.detection.pattern import (
    _file_regex_violations,
    _is_excluded,
    _per_line_violations,
    _require_regex_violations,
)
from guideline_checker.loader import RuleDetector

_SECONDS_PER_DAY = 86400


def _check_presence_rules(file_path: Path, lines: list[str], file_content: str, rule: str) -> list[Violation]:
    """Check whole-file presence requirements (must include X in every file)."""
    violations: list[Violation] = []
    rule_lower = rule.lower()
    suffix = file_path.suffix

    # "from __future__ import annotations in every file" (Python only)
    if (
        suffix == ".py"
        and "from __future__ import annotations" in rule_lower
        and "from __future__ import annotations" not in file_content
    ):
        violations.append(
            Violation(
                file=file_path,
                line_number=1,
                line_content="Missing: from __future__ import annotations",
                rule=rule,
                severity="warning",
            )
        )

    # "health endpoint is mandatory" / "/health endpoint" (Python/TS API files)
    if (
        suffix in (".py", ".ts")
        and "/health" in rule_lower
        and "mandatory" in rule_lower
        and '"/ health"' not in file_content
        and "'/health'" not in file_content
        and "@app" in file_content
    ):
        violations.append(
            Violation(
                file=file_path,
                line_number=1,
                line_content="Missing: /health endpoint (mandatory)",
                rule=rule,
                severity="warning",
            )
        )

    # Max function/method length
    match = re.search(r"max\s+function\s+length[:\s]+(\d+)", rule_lower) or re.search(
        r"max\s+(\d+)\s+lines?\s+(?:per\s+)?function", rule_lower
    )
    if match and suffix == ".py":
        limit = int(match.group(1))
        violations.extend(_check_function_lengths(file_path, lines, limit))

    return violations


def _declared_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
    root: Path | None = None,
) -> list[Violation]:
    """Run a rule's declarative detector. Severity is left as ``"warning"`` and
    overridden by the rule's own severity in :func:`_check_file`."""
    if detector.exclude and root is not None and _is_excluded(file_path, root, list(detector.exclude)):
        # A single rule opting out of paths its file-level glob still covers.
        # `assert` is a defect in a guard and the point of a test, and one glob
        # cannot say both.
        return []
    violations: list[Violation] = []
    violations.extend(_per_line_violations(file_path, lines, rule, detector))
    violations.extend(_file_regex_violations(file_path, lines, rule, detector))
    violations.extend(_require_regex_violations(file_path, lines, rule, detector))
    violations.extend(_cross_reference_violations(file_path, lines, rule, detector, root))
    violations.extend(_ast_violations(file_path, lines, rule, detector))
    violations.extend(_scan_violations(file_path, lines, rule, detector, root))
    violations.extend(_freshness_violations(file_path, rule, detector))
    violations.extend(_numeric_threshold_violations(file_path, lines, rule, detector))
    return violations


def _freshness_violations(file_path: Path, rule: str, detector: RuleDetector) -> list[Violation]:
    """Flag a matching file whose last modification is older than the threshold.

    The ``file-freshness`` mechanism (ADR D-0020): the engine measures a file's
    age; the ``stale_after_days`` value comes from the host's prose. Age is
    computed against the run's wall-clock, so PASS/FAIL is deterministic within a
    run but shifts over time as the file ages — which is the point of freshness.
    """
    if detector.stale_after_days is None:
        return []
    try:
        mtime = file_path.stat().st_mtime
    except OSError:  # file vanished between discovery and stat — nothing to flag
        return []
    age_days = (time.time() - mtime) / _SECONDS_PER_DAY
    if age_days <= detector.stale_after_days:
        return []
    return [
        Violation(
            file=file_path,
            line_number=1,
            line_content=f"last modified ~{int(age_days)}d ago (stale after {detector.stale_after_days}d)",
            rule=rule,
            severity="warning",
        ),
    ]
