"""Tests for the numeric-threshold mechanism (kind NUMERIC_THRESHOLD, ADR D-0021).

The engine owns the measuring (``guideline_checker.metrics``); the metric name and
the bound are host values read from the referential. A threshold literal inside
engine code would be the very drift ADR D-0016 draws its line against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines
from guideline_checker.kinds import CheckKind, kind_of_detector
from guideline_checker.loader import NumericThreshold, RuleDetector


def _referential(root: Path, detect_block: str) -> None:
    """Lay out a minimal referential whose single rule carries ``detect_block``."""
    (root / "guidelines").mkdir()
    (root / "guidelines" / "categories.yml").write_text(
        "categories:\n  - id: correctness\n    description: c\n", encoding="utf-8"
    )
    (root / "guidelines" / "languages").mkdir()
    (root / "guidelines" / "languages" / "python.yml").write_text(
        'language_target: "python"\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-bounded\n    category: correctness\n    severity: warning\n"
        f'    rule: "A measured metric stays under the bound"\n    detect:\n{detect_block}',
        encoding="utf-8",
    )


def _detector(root: Path) -> RuleDetector:
    instructions = load_yaml_guidelines(root)
    detectors = {r: d for instr in instructions for r, d in instr.rule_detectors.items()}
    return detectors["A measured metric stays under the bound"]


# ─── loader: the block validates into a NumericThreshold ──────────────────────


def test_loader_accepts_a_valid_block(tmp_path: Path) -> None:
    _referential(tmp_path, "      numeric_threshold:\n        metric: file_lines\n        max: 500\n")
    assert _detector(tmp_path).numeric_threshold == NumericThreshold(metric="file_lines", max_value=500)


def test_the_block_alone_is_a_detector(tmp_path: Path) -> None:
    """A threshold detects on its own — it must not be read as an empty ``detect:``."""
    _referential(tmp_path, "      numeric_threshold:\n        metric: function_lines\n        max: 50\n")
    assert _detector(tmp_path).numeric_threshold is not None


def test_loader_rejects_an_unknown_metric(tmp_path: Path) -> None:
    _referential(tmp_path, "      numeric_threshold:\n        metric: vibes\n        max: 3\n")
    with pytest.raises(GuidelineError, match="unknown metric"):
        load_yaml_guidelines(tmp_path)


def test_loader_rejects_a_missing_max(tmp_path: Path) -> None:
    """A metric with no bound measures without judging — half a rule is not a rule."""
    _referential(tmp_path, "      numeric_threshold:\n        metric: file_lines\n")
    with pytest.raises(GuidelineError, match="missing"):
        load_yaml_guidelines(tmp_path)


def test_loader_rejects_a_missing_metric(tmp_path: Path) -> None:
    _referential(tmp_path, "      numeric_threshold:\n        max: 500\n")
    with pytest.raises(GuidelineError, match="missing"):
        load_yaml_guidelines(tmp_path)


def test_loader_rejects_a_non_positive_max(tmp_path: Path) -> None:
    _referential(tmp_path, "      numeric_threshold:\n        metric: file_lines\n        max: 0\n")
    with pytest.raises(GuidelineError, match="positive integer"):
        load_yaml_guidelines(tmp_path)


def test_loader_rejects_a_boolean_max(tmp_path: Path) -> None:
    """``max: true`` is an int to Python and nonsense to a bound."""
    _referential(tmp_path, "      numeric_threshold:\n        metric: file_lines\n        max: true\n")
    with pytest.raises(GuidelineError, match="positive integer"):
        load_yaml_guidelines(tmp_path)


def test_loader_rejects_a_non_mapping_block(tmp_path: Path) -> None:
    _referential(tmp_path, "      numeric_threshold: 500\n")
    with pytest.raises(GuidelineError, match="must be a mapping"):
        load_yaml_guidelines(tmp_path)


# ─── kinds: the mechanism is classified ───────────────────────────────────────


def test_kind_is_numeric_threshold() -> None:
    assert kind_of_detector(RuleDetector(numeric_threshold=NumericThreshold("branches", 10))) is (
        CheckKind.NUMERIC_THRESHOLD
    )


def test_a_threshold_outranks_a_pattern_on_the_same_rule() -> None:
    """Measuring is the stronger claim: report it over a pattern shipped beside it."""
    detector = RuleDetector(forbid=("print(",), numeric_threshold=NumericThreshold("file_lines", 500))
    assert kind_of_detector(detector) is CheckKind.NUMERIC_THRESHOLD
