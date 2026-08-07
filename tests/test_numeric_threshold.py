"""Tests for the numeric-threshold mechanism (kind NUMERIC_THRESHOLD, ADR D-0021).

The engine owns the measuring (``guideline_checker.metrics``); the metric name and
the bound are host values read from the referential. A threshold literal inside
engine code would be the very drift ADR D-0016 draws its line against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import Violation, _numeric_threshold_violations
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


# ─── checker: the mechanism measures and fires ────────────────────────────────


def _measure(tmp_path: Path, source: str, threshold: NumericThreshold) -> list[Violation]:
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    return _numeric_threshold_violations(
        target,
        source.splitlines(),
        "A measured metric stays under the bound",
        RuleDetector(numeric_threshold=threshold),
    )


def test_a_file_over_the_bound_fires_at_line_one(tmp_path: Path) -> None:
    [violation] = _measure(tmp_path, "a = 1\nb = 2\nc = 3\nd = 4\n", NumericThreshold("file_lines", 3))
    assert violation.line_number == 1
    assert "4" in violation.line_content
    assert "3" in violation.line_content


def test_a_file_exactly_at_the_bound_does_not_fire(tmp_path: Path) -> None:
    """``max`` is a bound, not a target: reaching it is compliance, crossing it is not."""
    assert _measure(tmp_path, "a = 1\nb = 2\nc = 3\n", NumericThreshold("file_lines", 3)) == []


def test_a_long_function_fires_at_its_def_line(tmp_path: Path) -> None:
    source = "x = 0\n\n\ndef big():\n    a = 1\n    b = 2\n    return a + b\n"
    [violation] = _measure(tmp_path, source, NumericThreshold("function_lines", 2))
    assert violation.line_number == 4
    assert violation.line_content == "function 'big' measured 4 (max: 2)"


def test_each_function_over_the_bound_fires_once(tmp_path: Path) -> None:
    source = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    y = 1\n    return y\n"
    violations = _measure(tmp_path, source, NumericThreshold("function_lines", 2))
    assert len(violations) == 2


def test_a_branchy_function_fires_on_the_branch_metric(tmp_path: Path) -> None:
    source = "def f(x):\n    if x:\n        return 1\n    for _ in range(x):\n        pass\n    return 0\n"
    [violation] = _measure(tmp_path, source, NumericThreshold("branches", 2))
    assert "f" in violation.line_content


def test_a_detector_without_a_threshold_measures_nothing(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("a = 1\n" * 900, encoding="utf-8")
    assert _numeric_threshold_violations(target, ["a = 1"] * 900, "r", RuleDetector(forbid=("x",))) == []


def test_the_evidence_names_the_measurement_and_the_bound(tmp_path: Path) -> None:
    """A violation a human cannot act on is a violation they will baseline instead."""
    [violation] = _measure(tmp_path, "a = 1\nb = 2\n", NumericThreshold("file_lines", 1))
    assert violation.line_content == "file measured 2 (max: 1)"


# ─── the shipped referential arms the kind ────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_shipped_referential_arms_the_numeric_threshold_kind() -> None:
    """A kind in the taxonomy with no rule behind it is a mechanism that measures nothing."""
    instructions = load_yaml_guidelines(_REPO_ROOT)
    armed = {
        rule
        for instr in instructions
        for rule, detector in instr.rule_detectors.items()
        if kind_of_detector(detector) is CheckKind.NUMERIC_THRESHOLD
    }
    assert len(armed) >= 3


def test_the_engine_states_no_bound_of_its_own() -> None:
    """The bounds live in the host referential; restating one in engine code is the drift."""
    engine = (_REPO_ROOT / "guideline_checker" / "metrics.py").read_text(encoding="utf-8")
    assert "500" not in engine
    assert "max_value" not in engine


# ─── D-0021 blind spot: the numeric rules must see the repo's longest files ────
#
# ``.guidelineignore`` blanket-excluded checker.py and cli.py because a *pattern*
# detector's own tables hold the patterns it flags. That same blanket blinded the
# *measurement* rules to the two longest files in the repo — the exact silent-green
# this project refuses. The fix scopes the exclusion per-rule instead of per-file.

_BLIND_SPOT_FILES = ("guideline_checker/checker.py", "guideline_checker/cli.py")


def _shipped_detectors() -> dict[str, RuleDetector]:
    instructions = load_yaml_guidelines(_REPO_ROOT)
    return {rule: detector for instr in instructions for rule, detector in instr.rule_detectors.items()}


def test_the_numeric_rules_do_not_exclude_the_longest_files() -> None:
    """A measurement rule that skips the two longest files measures nothing worth measuring."""
    detectors = _shipped_detectors()
    for rule in (
        "A source file stays under the fleet file-length bound",
        "A function stays under the fleet function-length bound",
    ):
        assert not any(f in detectors[rule].exclude for f in _BLIND_SPOT_FILES)


def test_the_pattern_rules_scope_the_exclusion_per_rule() -> None:
    """checker.py and cli.py leave the blanket only because the pattern rules that match them opt out."""
    detectors = _shipped_detectors()
    assert "guideline_checker/checker.py" in detectors["Never use eval() or exec() on runtime data"].exclude
    assert "guideline_checker/cli.py" in detectors["Emit operational output through the logging module"].exclude
