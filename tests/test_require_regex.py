"""The ``detect.require_regex`` mechanism: absence is the violation.

Every other mechanism fires on something *present*. Rules like "an HTML page must
declare a viewport" or "the Makefile must define the mandatory targets" were
therefore inexpressible — ``file_regex`` says nothing when it finds nothing, even
though ``KIND_MEASURES`` has always advertised "matches (or fails to match)".
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import CheckKind, kind_of_detector, run_checks
from guideline_checker.guidelines import load_yaml_guidelines
from guideline_checker.loader import RuleDetector

_CATEGORIES = "categories:\n  - id: correctness\n    description: x\n"

_RULE_TEXT = "Declare a viewport meta tag"


def _referential(root: Path, rule_yaml: str) -> None:
    """Write a minimal HTML referential under ``root``."""
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages" / "html.yml").write_text(
        f'language_target: html\napply_to_glob: "**/*.html"\nrules:\n{rule_yaml}',
        encoding="utf-8",
    )


def _viewport_rule(*, severity: str = "error") -> str:
    return (
        f"  - id: html-needs-viewport\n    category: correctness\n    severity: {severity}\n"
        f'    rule: "{_RULE_TEXT}"\n'
        "    detect:\n      require_regex:\n        - '<meta[^>]+name=\"viewport\"'\n"
    )


def _violations_for(root: Path) -> list[str]:
    return [v.line_content for result in run_checks(root, all_sources=True) for v in result.violations]


def test_a_file_missing_the_required_pattern_is_flagged(tmp_path: Path) -> None:
    _referential(tmp_path, _viewport_rule())
    (tmp_path / "page.html").write_text("<html><head></head><body></body></html>\n", encoding="utf-8")

    flagged = [v for result in run_checks(tmp_path, all_sources=True) for v in result.violations]

    assert len(flagged) == 1
    assert flagged[0].line_number == 1  # an absence has no line of its own
    assert flagged[0].severity == "error"  # inherits the rule's severity, like every detector


def test_a_file_carrying_the_pattern_is_left_alone(tmp_path: Path) -> None:
    _referential(tmp_path, _viewport_rule())
    (tmp_path / "page.html").write_text(
        '<html><head><meta name="viewport" content="width=device-width"></head></html>\n',
        encoding="utf-8",
    )

    assert _violations_for(tmp_path) == []


def test_each_missing_pattern_reports_separately(tmp_path: Path) -> None:
    """Two requirements missing from one file must not collapse into one finding.

    The baseline fingerprints on rule + path + line content, so a bare empty
    content would make two distinct unmet requirements indistinguishable.
    """
    _referential(
        tmp_path,
        "  - id: html-needs-head\n    category: correctness\n    severity: warning\n"
        '    rule: "Declare a viewport and a lang attribute"\n'
        "    detect:\n      require_regex:\n"
        "        - '<meta[^>]+name=\"viewport\"'\n        - '<html[^>]+lang='\n",
    )
    (tmp_path / "page.html").write_text("<html><head></head></html>\n", encoding="utf-8")

    contents = _violations_for(tmp_path)

    assert len(contents) == 2
    assert len(set(contents)) == 2  # distinct fingerprints
    assert all(c.startswith("missing: ") for c in contents)


def test_the_mechanism_is_classified_as_file_content() -> None:
    detector = RuleDetector(require_regex=("<meta",))
    assert kind_of_detector(detector) is CheckKind.FILE_CONTENT


def test_a_child_rule_inherits_a_required_pattern_from_its_base(tmp_path: Path) -> None:
    (tmp_path / "guidelines" / "languages").mkdir(parents=True)
    (tmp_path / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (tmp_path / "guidelines" / "languages" / "html.yml").write_text(
        'language_target: html\napply_to_glob: "**/*.html"\nrules:\n'
        "  - id: base-viewport\n    category: correctness\n    severity: warning\n"
        f'    rule: "{_RULE_TEXT}"\n'
        "    detect:\n      require_regex:\n        - '<meta[^>]+name=\"viewport\"'\n"
        "  - id: html-needs-viewport\n    extends: base-viewport\n    severity: error\n",
        encoding="utf-8",
    )

    instructions = load_yaml_guidelines(tmp_path)
    detectors = {rule: d for i in instructions for rule, d in i.rule_detectors.items()}

    assert detectors[_RULE_TEXT].require_regex == ('<meta[^>]+name="viewport"',)


def test_extends_no_longer_drops_an_inherited_scanner(tmp_path: Path) -> None:
    """Regression: ``_merge_detectors`` rebuilt the detector field by field and
    omitted ``scan_checks``, so a base's scanner was dropped.

    The child must carry a ``detect:`` block of its own — that is what forces the
    merge. With no child detector the base passes through untouched and the bug
    stays hidden, which is exactly how it survived.
    """
    (tmp_path / "guidelines" / "languages").mkdir(parents=True)
    (tmp_path / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (tmp_path / "guidelines" / "languages" / "python.yml").write_text(
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: base-secret\n    category: correctness\n    severity: error\n"
        '    rule: "No hardcoded credentials"\n'
        "    detect:\n      scan:\n        - secret-assignment\n"
        "  - id: py-no-secret\n    extends: base-secret\n    severity: error\n"
        "    detect:\n      forbid:\n        - 'API_KEY ='\n",
        encoding="utf-8",
    )

    instructions = load_yaml_guidelines(tmp_path)
    detectors = {rule: d for i in instructions for rule, d in i.rule_detectors.items()}
    merged = detectors["No hardcoded credentials"]

    assert merged.forbid == ("API_KEY =",)  # the child's own detector survived
    assert merged.scan_checks == ("secret-assignment",)  # and so did the base's scanner
