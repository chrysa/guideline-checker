"""Tests for the file-freshness mechanism (kind FILE_FRESHNESS, ADR D-0020)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from guideline_checker.checker import _freshness_violations
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines
from guideline_checker.kinds import CheckKind, kind_of_detector
from guideline_checker.loader import RuleDetector

_DAY = 86400


def _aged(tmp_path: Path, name: str, days_old: float) -> Path:
    p = tmp_path / name
    p.write_text("x", encoding="utf-8")
    old = time.time() - days_old * _DAY
    os.utime(p, (old, old))
    return p


def test_stale_file_is_flagged(tmp_path: Path) -> None:
    p = _aged(tmp_path, "old.md", days_old=120)
    violations = _freshness_violations(p, "Docs must be refreshed", RuleDetector(stale_after_days=90))
    assert len(violations) == 1
    assert "stale after 90d" in violations[0].line_content


def test_fresh_file_is_not_flagged(tmp_path: Path) -> None:
    p = _aged(tmp_path, "new.md", days_old=10)
    assert _freshness_violations(p, "r", RuleDetector(stale_after_days=90)) == []


def test_no_freshness_threshold_means_no_check(tmp_path: Path) -> None:
    p = _aged(tmp_path, "any.md", days_old=999)
    assert _freshness_violations(p, "r", RuleDetector(forbid=("x",))) == []


def test_kind_is_file_freshness() -> None:
    assert kind_of_detector(RuleDetector(stale_after_days=90)) is CheckKind.FILE_FRESHNESS


def _referential(root: Path, detect_block: str) -> None:
    (root / "guidelines").mkdir()
    (root / "guidelines" / "categories.yml").write_text(
        "categories:\n  - id: correctness\n    description: c\n", encoding="utf-8"
    )
    (root / "guidelines" / "docs").mkdir()
    (root / "guidelines" / "docs" / "docs.yml").write_text(
        'language_target: "*"\napply_to_glob: "**/*.md"\nrules:\n'
        "  - id: docs-fresh\n    category: correctness\n    severity: warning\n"
        f'    rule: "Docs kept fresh"\n    detect:\n{detect_block}',
        encoding="utf-8",
    )


def test_loader_accepts_stale_after_days(tmp_path: Path) -> None:
    _referential(tmp_path, "      stale_after_days: 90\n")
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {r: d for instr in instructions for r, d in instr.rule_detectors.items()}
    assert detectors["Docs kept fresh"].stale_after_days == 90


def test_loader_rejects_non_positive_freshness(tmp_path: Path) -> None:
    _referential(tmp_path, "      stale_after_days: 0\n")
    with pytest.raises(GuidelineError, match="stale_after_days"):
        load_yaml_guidelines(tmp_path)
