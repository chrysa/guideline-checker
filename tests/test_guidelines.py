"""Tests for the structured YAML rule referential loader (guidelines/)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from guideline_checker.checker import run_checks
from guideline_checker.guidelines import (
    GuidelineError,
    _target_to_glob,
    load_yaml_guidelines,
)
from guideline_checker.loader import SourceType, load_all_sources

# The repository's own shipped guidelines/ tree (for coverage assertions).
_REPO_ROOT = Path(__file__).resolve().parents[1]

_CATEGORIES = """\
categories:
  - id: stack
    description: "Stack choices"
  - id: architecture
    description: "Architecture"
  - id: prompt-format
    description: "Prompt formatting"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def referential(tmp_path: Path) -> Path:
    """A minimal but complete guidelines/ tree across both dimensions."""
    root = tmp_path
    g = root / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "ai-models" / "claude.yml",
        "model_target: claude\nrules:\n"
        "  - id: claude-xml\n    category: prompt-format\n    severity: warning\n"
        '    rule: "Structure prompts with XML tags"\n    rationale: "Better adherence"\n',
    )
    _write(
        g / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-pydantic\n    category: stack\n    severity: error\n"
        '    rule: "Pydantic v2 only"\n',
    )
    return root


def test_loads_both_dimensions(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    assert {i.source_type for i in instructions} == {SourceType.GUIDELINES_YAML}
    rules = {r for i in instructions for r in i.rules}
    assert rules == {"Structure prompts with XML tags", "Pydantic v2 only"}


def test_target_drives_apply_to(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    by_rule = {i.rules[0]: i for i in instructions}
    # Language file declares its glob; the model file declares none -> all files.
    assert by_rule["Pydantic v2 only"].apply_to == "**/*.py"
    assert by_rule["Structure prompts with XML tags"].apply_to == "**/*"


def test_explicit_severity_recorded(referential: Path) -> None:
    instructions = load_yaml_guidelines(referential)
    by_rule = {i.rules[0]: i for i in instructions}
    assert by_rule["Pydantic v2 only"].rule_severity["Pydantic v2 only"] == "error"


@pytest.mark.parametrize(
    ("target", "file_glob", "expected"),
    [
        ("python", "**/*.py", "**/*.py"),
        ("typescript", "**/*.ts,**/*.tsx", "**/*.ts,**/*.tsx"),
        ("django", "**/*.py", "**/*.py"),  # an arbitrary new target uses its file glob
        ("*", "**/*.py", "**/*"),  # the wildcard ignores the file glob
        ("claude", "", "**/*"),  # no file glob -> all files
    ],
)
def test_target_to_glob(target: str, file_glob: str, expected: str) -> None:
    assert _target_to_glob(target, file_glob) == expected


def test_per_rule_target_override(tmp_path: Path) -> None:
    """A rule overriding language_target='*' inside python.yml applies to all files."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        '  - id: py-specific\n    category: stack\n    severity: warning\n    rule: "Python only rule"\n'
        '  - id: transverse\n    language_target: "*"\n    category: stack\n    severity: warning\n'
        '    rule: "Applies everywhere"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    globs = {i.rules[0]: i.apply_to for i in instructions}
    assert globs["Python only rule"] == "**/*.py"
    assert globs["Applies everywhere"] == "**/*"


def test_standard_field_traces_to_rule_id(tmp_path: Path) -> None:
    """A rule carrying `standard:` exposes it via rule_standard; a rule without it
    has no entry (GV-012 traceability — mirrors provenance)."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-mapped\n    category: stack\n    severity: warning\n"
        '    standard: FE-070\n    rule: "A mapped rule"\n'
        "  - id: py-generic\n    category: stack\n    severity: warning\n"
        '    rule: "A generic rule"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    merged: dict[str, str] = {}
    for instruction in instructions:
        merged.update(instruction.rule_standard)
    assert merged == {"A mapped rule": "FE-070"}


def test_new_dimension_is_folder_driven(tmp_path: Path) -> None:
    """A brand-new dimension folder loads with ZERO code change (genericity guard)."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "frameworks" / "django.yml",
        'framework_target: django\napply_to_glob: "**/*.py"\nrules:\n'
        '  - id: dj-orm\n    category: stack\n    severity: warning\n    rule: "Use the ORM, not raw SQL"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    by_rule = {r: i for i in instructions for r in i.rules}
    assert "Use the ORM, not raw SQL" in by_rule
    instr = by_rule["Use the ORM, not raw SQL"]
    assert instr.apply_to == "**/*.py"
    assert instr.description == "Guidelines — frameworks/django [django]"


def test_file_without_target_field_is_transverse(tmp_path: Path) -> None:
    """A file with no <dim>_target key defaults every rule to the wildcard target."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "misc" / "anything.yml",
        'rules:\n  - id: t1\n    category: stack\n    severity: info\n    rule: "Everywhere"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    by_rule = {r: i for i in instructions for r in i.rules}
    assert by_rule["Everywhere"].apply_to == "**/*"


def test_multiple_target_fields_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "bad.yml",
        "language_target: python\nmodel_target: claude\nrules:\n"
        '  - id: x\n    category: stack\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match="multiple target fields"):
        load_yaml_guidelines(tmp_path)


def test_apply_to_glob_defaults_to_all_files(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"  # no apply_to_glob declared
        '  - id: p\n    category: stack\n    severity: warning\n    rule: "P"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    by_rule = {r: i for i in instructions for r in i.rules}
    assert by_rule["P"].apply_to == "**/*"


def test_invalid_apply_to_glob_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        "language_target: python\napply_to_glob: 123\nrules:\n"
        '  - id: p\n    category: stack\n    severity: warning\n    rule: "P"\n',
    )
    with pytest.raises(GuidelineError, match="apply_to_glob"):
        load_yaml_guidelines(tmp_path)


def test_unknown_category_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: bad\n    category: nonexistent\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match="unknown category"):
        load_yaml_guidelines(tmp_path)


def test_invalid_severity_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: bad\n    category: stack\n    severity: critical\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match="invalid severity"):
        load_yaml_guidelines(tmp_path)


def test_missing_categories_file_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: x\n    category: stack\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match=r"categories\.yml"):
        load_yaml_guidelines(tmp_path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(g / "languages" / "python.yml", "language_target: python\nrules:\n  - id: x\n  bad: : :\n")
    with pytest.raises(GuidelineError, match="invalid YAML"):
        load_yaml_guidelines(tmp_path)


def test_duplicate_id_first_match_wins_and_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    # "_common.yml" is parsed first (sorted), so its rule wins the id tie.
    _write(
        g / "languages" / "_common.yml",
        'language_target: "*"\nrules:\n  - id: dup\n    category: stack\n    severity: error\n    rule: "First wins"\n',
    )
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        '  - id: dup\n    category: stack\n    severity: warning\n    rule: "Second skipped"\n',
    )
    with caplog.at_level(logging.WARNING):
        instructions = load_yaml_guidelines(tmp_path)
    rules = {r for i in instructions for r in i.rules}
    assert rules == {"First wins"}
    assert "duplicate rule id 'dup'" in caplog.text


def test_intra_file_duplicate_id_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    # Same id twice in ONE file = authoring bug, no override intent — hard fail.
    _write(
        g / "languages" / "python.yml",
        "language_target: python\nrules:\n"
        '  - id: dup\n    category: stack\n    severity: error\n    rule: "First"\n'
        '  - id: dup\n    category: stack\n    severity: warning\n    rule: "Second"\n',
    )
    with pytest.raises(GuidelineError, match="duplicate rule id 'dup' within the file"):
        load_yaml_guidelines(tmp_path)


def test_unknown_category_error_lists_known_categories(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\nrules:\n  - id: bad\n    category: nonexistent\n    severity: error\n    rule: "X"\n',
    )
    with pytest.raises(GuidelineError, match=r"known categories:.*architecture.*prompt-format.*stack"):
        load_yaml_guidelines(tmp_path)


def test_no_guidelines_dir_returns_empty(tmp_path: Path) -> None:
    assert load_yaml_guidelines(tmp_path) == []


def test_markdown_sources_have_empty_rule_severity(tmp_path: Path) -> None:
    """Regression: markdown-only projects keep rule_severity empty (no behaviour change)."""
    _write(tmp_path / "CLAUDE.md", "# Rules\n\n- No print() in production code\n")
    instructions = load_all_sources(tmp_path)
    assert instructions
    assert all(i.rule_severity == {} for i in instructions)


def test_severity_override_end_to_end(tmp_path: Path) -> None:
    """A detectable YAML rule reports the YAML severity, not the phrasing default.

    The phrasing "no print" defaults to *warning* in the engine; the YAML pins it
    to *error*, so the produced violation must carry severity == "error".
    """
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-no-print\n    category: stack\n    severity: error\n"
        '    rule: "No print() in production code"\n',
    )
    _write(tmp_path / "src" / "app.py", "def main():\n    print('hello')\n")

    results = run_checks(tmp_path, all_sources=True)
    violations = [v for r in results for v in r.violations if "print" in v.rule.lower()]
    assert violations, "expected the detectable no-print rule to flag print("
    assert all(v.severity == "error" for v in violations)


def test_shipped_referential_covers_spec_targets() -> None:
    """The repo's own guidelines/ ships each shipped language dimension.

    ADR D-0016: the tool ships only generic, detector-backed rules; the former
    ``ai-models/`` prose dimension (semantic advice with no detector) was
    dropped — such rules belong to a host's own prose, not the shipped tool.
    """
    instructions = load_yaml_guidelines(_REPO_ROOT)
    descriptions = {i.description for i in instructions}
    for dim, stem in [("languages", "react")]:
        assert f"Guidelines — {dim}/{stem} [{stem}]" in descriptions
    react = next(i for i in instructions if i.description == "Guidelines — languages/react [react]")
    assert react.apply_to == "**/*.tsx,**/*.jsx"
    # No ai-models dimension is shipped any more (D-0016).
    assert not any("ai-models/" in d for d in descriptions)


# --- L1.1 secret-scanner via detect.scan ---

_FAKE_KEY_LINE = 'api_key = "Zx9Qm2Lp7Vt4Rk8Nw1Yb6Hs3DfAa5Cc"\n'


def test_detect_scan_accepted_and_recorded(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "_common.yml",
        'language_target: "*"\nrules:\n  - id: secrets\n    category: stack\n    severity: error\n'
        '    rule: "Secrets must come from configuration"\n    detect:\n      scan: [secret-assignment]\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    detector = next(i.rule_detectors["Secrets must come from configuration"] for i in instructions if i.rule_detectors)
    assert detector.scan_checks == ("secret-assignment",)


def test_detect_scan_unknown_scanner_raises(tmp_path: Path) -> None:
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "_common.yml",
        'language_target: "*"\nrules:\n  - id: secrets\n    category: stack\n    severity: error\n'
        '    rule: "X"\n    detect:\n      scan: [no-such-scanner]\n',
    )
    with pytest.raises(GuidelineError, match="unknown scanner"):
        load_yaml_guidelines(tmp_path)


class TestSecretScanEndToEnd:
    def _ref(self, tmp_path: Path) -> Path:
        g = tmp_path / "guidelines"
        _write(g / "categories.yml", _CATEGORIES)
        _write(
            g / "languages" / "_common.yml",
            'language_target: "*"\nrules:\n  - id: secrets\n    category: stack\n    severity: error\n'
            '    rule: "Secrets must come from configuration"\n    detect:\n      scan: [secret-assignment]\n',
        )
        return tmp_path

    def _hits(self, root: Path) -> list:
        results = run_checks(root, all_sources=True)
        return [v for r in results for v in r.violations if v.rule == "Secrets must come from configuration"]

    def test_flags_real_secret_as_error(self, tmp_path: Path) -> None:
        root = self._ref(tmp_path)
        _write(root / "src" / "app.py", _FAKE_KEY_LINE)
        hits = self._hits(root)
        assert len(hits) == 1
        assert hits[0].severity == "error"

    def test_allowlisted_path_skipped(self, tmp_path: Path) -> None:
        root = self._ref(tmp_path)
        _write(root / "pkg" / "fixture.py", _FAKE_KEY_LINE)
        _write(root / ".secrets-allowlist", "paths:\n  - pkg/**\n")
        assert self._hits(root) == []

    def test_allowlisted_value_skipped(self, tmp_path: Path) -> None:
        root = self._ref(tmp_path)
        _write(root / "src" / "app.py", 'secret = "super-secret-should-not-leak"\n')
        _write(root / ".secrets-allowlist", "values:\n  - super-secret-should-not-leak\n")
        assert self._hits(root) == []

    def test_inline_disable_skipped(self, tmp_path: Path) -> None:
        root = self._ref(tmp_path)
        _write(root / "src" / "app.py", 'api_key = "Zx9Qm2Lp7Vt4Rk8Nw1Yb6Hs3DfAa5Cc"  # guideline: disable\n')
        assert self._hits(root) == []

    def test_env_lookup_not_flagged(self, tmp_path: Path) -> None:
        root = self._ref(tmp_path)
        _write(root / "src" / "app.py", 'api_key = "${API_KEY}"\n')
        assert self._hits(root) == []


# --- L1.4 rule inheritance via extends: ---


def _py_referential(tmp_path: Path, rules_yaml: str) -> Path:
    """A python.yml referential whose rules block is supplied verbatim."""
    g = tmp_path / "guidelines"
    _write(g / "categories.yml", _CATEGORIES)
    _write(
        g / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n' + rules_yaml,
    )
    return tmp_path


def _rule_severity(instructions: list, rule_text: str) -> str | None:
    """Return the reported severity for a rule across all instruction files."""
    for instr in instructions:
        if rule_text in instr.rule_severity:
            return instr.rule_severity[rule_text]
    return None


def _all_rules(instructions: list) -> set[str]:
    return {r for i in instructions for r in i.rules}


class TestRuleInheritance:
    """L1.4 — ``extends:`` composition (same-file, union detect, abstract bases)."""

    def test_child_inherits_scalar_fields(self, tmp_path: Path) -> None:
        # Child declares only its own rule text + extends; category/severity inherited.
        root = _py_referential(
            tmp_path,
            "  - id: base-print\n    category: stack\n    severity: error\n"
            '    rule: "No print"\n    detect: {forbid: ["print("]}\n'
            "  - id: child-print\n    extends: base-print\n"
            '    rule: "No print in child code"\n',
        )
        instructions = load_yaml_guidelines(root)
        assert _rule_severity(instructions, "No print in child code") == "error"

    def test_child_overrides_severity(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            "  - id: base-abs\n    abstract: true\n    category: stack\n    severity: error\n"
            '    rule: "Base"\n    detect: {forbid: ["AAA"]}\n'
            "  - id: child-warn\n    extends: base-abs\n    severity: warning\n"
            '    rule: "Child warns"\n',
        )
        instructions = load_yaml_guidelines(root)
        assert _rule_severity(instructions, "Child warns") == "warning"

    def test_abstract_base_not_emitted(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            "  - id: base-abs\n    abstract: true\n    category: stack\n    severity: error\n"
            '    rule: "Abstract base rule"\n    detect: {forbid: ["AAA"]}\n'
            "  - id: child\n    extends: base-abs\n"
            '    rule: "Concrete child rule"\n',
        )
        rules = _all_rules(load_yaml_guidelines(root))
        assert "Concrete child rule" in rules
        assert "Abstract base rule" not in rules

    def test_union_detect_fires_on_both_inherited_and_own_patterns(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            "  - id: base-abs\n    abstract: true\n    category: stack\n    severity: error\n"
            '    rule: "Base"\n    detect: {forbid: ["AAA"]}\n'
            "  - id: child\n    extends: base-abs\n"
            '    rule: "No forbidden tokens"\n    detect: {forbid: ["BBB"]}\n',
        )
        _write(root / "src" / "inherited.py", "x = 'AAA'\n")
        _write(root / "src" / "own.py", "y = 'BBB'\n")
        results = run_checks(root, all_sources=True)
        flagged = {v.file.name for r in results for v in r.violations if v.rule == "No forbidden tokens"}
        # Union: child fires on the inherited base pattern AND its own pattern.
        assert flagged == {"inherited.py", "own.py"}

    def test_extends_chain_unions_all_levels(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            "  - id: a\n    abstract: true\n    category: stack\n    severity: error\n"
            '    rule: "A"\n    detect: {forbid: ["AAA"]}\n'
            "  - id: b\n    abstract: true\n    extends: a\n"
            '    rule: "B"\n    detect: {forbid: ["BBB"]}\n'
            "  - id: c\n    extends: b\n"
            '    rule: "Chain leaf"\n    detect: {forbid: ["CCC"]}\n',
        )
        _write(root / "src" / "f.py", "v = 'AAA' + 'BBB' + 'CCC'\n")
        results = run_checks(root, all_sources=True)
        hits = sum(1 for r in results for v in r.violations if v.rule == "Chain leaf")
        assert hits >= 1

    def test_abstract_scalar_only_template(self, tmp_path: Path) -> None:
        # Abstract base with no detect block; child supplies the detector.
        root = _py_referential(
            tmp_path,
            "  - id: base-scalar\n    abstract: true\n    category: stack\n    severity: warning\n"
            '    rule: "Scalar template"\n'
            "  - id: child\n    extends: base-scalar\n"
            '    rule: "No TODO markers"\n    detect: {forbid: ["TODO"]}\n',
        )
        _write(root / "src" / "f.py", "# TODO fix\n")
        results = run_checks(root, all_sources=True)
        violations = [v for r in results for v in r.violations if v.rule == "No TODO markers"]
        assert violations
        assert all(v.severity == "warning" for v in violations)

    def test_unknown_base_raises(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            '  - id: child\n    extends: does-not-exist\n    rule: "Orphan"\n    detect: {forbid: ["X"]}\n',
        )
        with pytest.raises(GuidelineError, match="extends unknown base 'does-not-exist'"):
            load_yaml_guidelines(root)

    def test_extends_cycle_raises(self, tmp_path: Path) -> None:
        root = _py_referential(
            tmp_path,
            '  - id: a\n    extends: b\n    category: stack\n    severity: error\n    rule: "A"\n'
            '  - id: b\n    extends: a\n    category: stack\n    severity: error\n    rule: "B"\n',
        )
        with pytest.raises(GuidelineError, match="cycle"):
            load_yaml_guidelines(root)

    def test_child_missing_inherited_required_field_resolves(self, tmp_path: Path) -> None:
        # Child omits category entirely; it must resolve from the base, not raise.
        root = _py_referential(
            tmp_path,
            "  - id: base\n    category: architecture\n    severity: info\n"
            '    rule: "Base"\n    detect: {forbid: ["AAA"]}\n'
            "  - id: child\n    extends: base\n"
            '    rule: "Child no category"\n',
        )
        instructions = load_yaml_guidelines(root)
        assert "Child no category" in _all_rules(instructions)

    def test_missing_required_without_extends_still_raises(self, tmp_path: Path) -> None:
        # Regression: a rule with no extends must still declare category/severity/rule.
        root = _py_referential(
            tmp_path,
            '  - id: lonely\n    rule: "No category nor severity"\n',
        )
        with pytest.raises(GuidelineError):
            load_yaml_guidelines(root)
