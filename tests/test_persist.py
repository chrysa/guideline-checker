"""Tests for persisting a validated detector onto a YAML rule.

Closing the workshop loop: once a proposal has been proven in the sandbox, the
user validates it and the detector is written onto its rule in
``guidelines/<dim>/*.yml``. ``dry_run`` shows the unified diff and writes
nothing; applying it must round-trip back through the real loader so the newly
armed rule is picked up on the next scan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.guidelines import load_yaml_guidelines
from guideline_checker.loader import RuleDetector
from guideline_checker.workshop.persist import apply_detector, detector_to_detect

_CATEGORIES = "categories:\n  - id: correctness\n    description: Correctness\n"


def _referential(root: Path) -> None:
    (root / "guidelines").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "languages" / "python.yml").write_text(
        "# Python rules.\n"
        "language_target: python\n"
        'apply_to_glob: "**/*.py"\n'
        "rules:\n"
        "  - id: py-no-print\n"
        "    category: correctness\n"
        "    severity: warning\n"
        '    rule: "Never use print for debugging output"\n',
        encoding="utf-8",
    )


def test_detector_to_detect_maps_every_field() -> None:
    detect = detector_to_detect(RuleDetector(forbid=("print(",), ast_checks=("pydantic-v1",), match_in_comments=True))
    assert detect == {"forbid": ["print("], "ast": ["pydantic-v1"], "match_in_comments": True}


def test_dry_run_shows_diff_and_writes_nothing(tmp_path: Path) -> None:
    _referential(tmp_path)
    target = tmp_path / "guidelines" / "languages" / "python.yml"
    before = target.read_text(encoding="utf-8")

    result = apply_detector(tmp_path, "py-no-print", RuleDetector(forbid=("print(",)), dry_run=True)

    assert result.written is False
    assert "detect" in result.diff and "print(" in result.diff
    assert target.read_text(encoding="utf-8") == before


def test_apply_writes_and_round_trips_through_loader(tmp_path: Path) -> None:
    _referential(tmp_path)

    result = apply_detector(tmp_path, "py-no-print", RuleDetector(forbid=("print(",)), dry_run=False)

    assert result.written is True
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {r: d for instr in instructions for r, d in instr.rule_detectors.items()}
    armed = next(d for rule, d in detectors.items() if "print" in rule.lower())
    assert "print(" in armed.forbid


def test_leading_comment_is_preserved(tmp_path: Path) -> None:
    _referential(tmp_path)
    target = tmp_path / "guidelines" / "languages" / "python.yml"

    apply_detector(tmp_path, "py-no-print", RuleDetector(forbid=("print(",)), dry_run=False)

    assert target.read_text(encoding="utf-8").startswith("# Python rules.")


def test_unknown_rule_id_raises(tmp_path: Path) -> None:
    _referential(tmp_path)
    with pytest.raises(KeyError):
        apply_detector(tmp_path, "does-not-exist", RuleDetector(forbid=("x",)), dry_run=True)


def test_provenance_is_stamped_and_round_trips(tmp_path: Path) -> None:
    """ADR D-0016: arming a detector records the host prose sentence it derives
    from, and the referential still loads cleanly with that annotation."""
    _referential(tmp_path)
    target = tmp_path / "guidelines" / "languages" / "python.yml"
    sentence = "Never use print for debugging output"

    result = apply_detector(
        tmp_path,
        "py-no-print",
        RuleDetector(forbid=("print(",)),
        dry_run=False,
        provenance=sentence,
    )

    assert result.written is True
    text = target.read_text(encoding="utf-8")
    assert "provenance:" in text and sentence in text
    # The annotation must not break the real loader.
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {r: d for instr in instructions for r, d in instr.rule_detectors.items()}
    assert any("print(" in d.forbid for d in detectors.values())


def test_no_provenance_leaves_rule_unannotated(tmp_path: Path) -> None:
    _referential(tmp_path)
    target = tmp_path / "guidelines" / "languages" / "python.yml"

    apply_detector(tmp_path, "py-no-print", RuleDetector(forbid=("print(",)), dry_run=False)

    assert "provenance:" not in target.read_text(encoding="utf-8")


def test_find_rule_id_for_text_matches(tmp_path: Path) -> None:
    from guideline_checker.workshop.persist import find_rule_id_for_text

    _referential(tmp_path)
    assert find_rule_id_for_text(tmp_path, "Never use print for debugging output") == "py-no-print"


def test_find_rule_id_for_text_none_when_absent(tmp_path: Path) -> None:
    from guideline_checker.workshop.persist import find_rule_id_for_text

    _referential(tmp_path)
    assert find_rule_id_for_text(tmp_path, "some markdown bullet with no yaml rule") is None
    # No guidelines/ dir at all -> None, never raises.
    assert find_rule_id_for_text(tmp_path / "nope", "x") is None


def test_rule_detector_json_round_trips() -> None:
    """A fully-populated RuleDetector must survive a JSON round-trip.

    Guards the plan's assumption that detectors serialize to JSON: every field
    (including the nested CrossReference / NumericThreshold) is a plain
    str/tuple/int/bool. If a future field is a callable or a compiled Pattern,
    ``json.dumps`` raises here instead of failing silently in the derived cache.
    """
    import dataclasses
    import json

    from guideline_checker.loader import CrossReference, NumericThreshold

    detector = RuleDetector(
        forbid=("print(",),
        forbid_regex=(r"\bTODO\b",),
        file_regex=(r"(?s)class .*",),
        require_regex=(r"response_model=",),
        ast_checks=("pydantic-v1",),
        scan_checks=("hardcoded-secret",),
        cross_reference=CrossReference(cite=r"`(?P<name>\w+)`", define_in=("docs/x.md",), define_as="{name}"),
        stale_after_days=30,
        numeric_threshold=NumericThreshold(metric="coverage", max_value=85),
        exclude=("tests/",),
        match_in_comments=True,
    )

    payload = json.dumps(dataclasses.asdict(detector))
    data = json.loads(payload)
    data["forbid"] = tuple(data["forbid"])
    data["forbid_regex"] = tuple(data["forbid_regex"])
    data["file_regex"] = tuple(data["file_regex"])
    data["require_regex"] = tuple(data["require_regex"])
    data["ast_checks"] = tuple(data["ast_checks"])
    data["scan_checks"] = tuple(data["scan_checks"])
    data["exclude"] = tuple(data["exclude"])
    data["cross_reference"] = CrossReference(
        cite=data["cross_reference"]["cite"],
        define_in=tuple(data["cross_reference"]["define_in"]),
        define_as=data["cross_reference"]["define_as"],
    )
    data["numeric_threshold"] = NumericThreshold(**data["numeric_threshold"])

    assert RuleDetector(**data) == detector
