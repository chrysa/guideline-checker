"""Tests for the MECHANISMS taxonomy (ADR D-0020)."""

from __future__ import annotations

from guideline_checker.core.detection.kinds import CheckKind, kind_of_detector, kind_of_phrase
from guideline_checker.loader import RuleDetector


class TestKindOfDetector:
    def test_none_detector_is_none(self) -> None:
        assert kind_of_detector(None) is None

    def test_empty_detector_is_none(self) -> None:
        assert kind_of_detector(RuleDetector()) is None

    def test_forbid_maps_to_forbidden_pattern(self) -> None:
        assert kind_of_detector(RuleDetector(forbid=("print(",))) is CheckKind.FORBIDDEN_PATTERN

    def test_forbid_regex_maps_to_forbidden_pattern(self) -> None:
        assert kind_of_detector(RuleDetector(forbid_regex=("import \\*",))) is CheckKind.FORBIDDEN_PATTERN

    def test_file_regex_maps_to_file_content(self) -> None:
        assert kind_of_detector(RuleDetector(file_regex=("^TODO",))) is CheckKind.FILE_CONTENT

    def test_ast_maps_to_ast_structure(self) -> None:
        assert kind_of_detector(RuleDetector(ast_checks=("pydantic-v1",))) is CheckKind.AST_STRUCTURE

    def test_scan_maps_to_content_scan(self) -> None:
        assert kind_of_detector(RuleDetector(scan_checks=("secret-assignment",))) is CheckKind.CONTENT_SCAN

    def test_ast_wins_over_pattern_when_both_present(self) -> None:
        detector = RuleDetector(forbid=("x",), ast_checks=("pydantic-v1",))
        assert kind_of_detector(detector) is CheckKind.AST_STRUCTURE


class TestKindOfPhrase:
    def test_numeric_threshold_prose(self) -> None:
        assert kind_of_phrase("Maximum file length: 500 lines") is CheckKind.NUMERIC_THRESHOLD
        assert kind_of_phrase("Test coverage must be at least 85") is CheckKind.NUMERIC_THRESHOLD

    def test_presence_prose(self) -> None:
        assert kind_of_phrase("A README.md must exist in every package") is CheckKind.FILE_PRESENCE

    def test_other_prose_defaults_to_forbidden_pattern(self) -> None:
        assert kind_of_phrase("No print() calls in production code") is CheckKind.FORBIDDEN_PATTERN
