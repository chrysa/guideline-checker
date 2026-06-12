"""Tests for declarative per-rule detectors (the YAML ``detect:`` block).

A structured rule may carry a ``detect:`` block declaring how it is detected,
instead of relying on the checker recognising its prose. These tests cover the
loader-side parsing/validation and the end-to-end detection path, plus a
drift-guard on the three shipped Python rules that now use the mechanism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import run_checks
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines

_REPO_ROOT = Path(__file__).resolve().parents[1]

_CATEGORIES = """\
categories:
  - id: stack
    description: "Stack choices"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _referential(root: Path, rule_yaml: str) -> None:
    """Lay down a minimal guidelines/ tree with one python rule file."""
    _write(root / "guidelines" / "categories.yml", _CATEGORIES)
    _write(
        root / "guidelines" / "languages" / "python.yml",
        f'language_target: python\napply_to_glob: "**/*.py"\nrules:\n{rule_yaml}',
    )


# ─── Parsing / validation ─────────────────────────────────────────────────────


def test_detect_block_populates_rule_detectors(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-no-foo\n    category: stack\n    severity: error\n"
        '    rule: "Forbid foo"\n'
        "    detect:\n      forbid:\n        - 'foo('\n",
    )
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {rule: det for i in instructions for rule, det in i.rule_detectors.items()}
    assert "Forbid foo" in detectors
    assert detectors["Forbid foo"].forbid == ("foo(",)


def test_absent_detect_leaves_rule_detectors_empty(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        '  - id: py-plain\n    category: stack\n    severity: warning\n    rule: "No detect block"\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    assert all(i.rule_detectors == {} for i in instructions)


@pytest.mark.parametrize(
    "detect_yaml",
    [
        "    detect: 'not-a-mapping'\n",  # detect must be a mapping
        "    detect:\n      forbid: 'not-a-list'\n",  # pattern key must be a list
        "    detect:\n      forbid:\n        - ''\n",  # entries must be non-empty
        "    detect:\n      forbid: []\n",  # block declares no patterns
        "    detect:\n      bogus:\n        - 'x'\n",  # unknown key
        "    detect:\n      forbid:\n        - 'x'\n      match_in_comments: 'yes'\n",  # non-bool flag
    ],
)
def test_malformed_detect_raises(tmp_path: Path, detect_yaml: str) -> None:
    _referential(
        tmp_path,
        '  - id: py-bad\n    category: stack\n    severity: error\n    rule: "Bad detect"\n' + detect_yaml,
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


# ─── End-to-end detection ─────────────────────────────────────────────────────


def test_forbid_flags_line_with_rule_severity(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-no-foo\n    category: stack\n    severity: error\n"
        '    rule: "Forbid foo"\n'
        "    detect:\n      forbid:\n        - 'foo('\n",
    )
    _write(tmp_path / "src" / "app.py", "def main():\n    foo(1)\n")
    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if v.rule == "Forbid foo"]
    assert flagged
    assert all(v.severity == "error" for v in flagged)


def test_forbid_clean_file_no_violation(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-no-foo\n    category: stack\n    severity: error\n"
        '    rule: "Forbid foo"\n'
        "    detect:\n      forbid:\n        - 'foo('\n",
    )
    _write(tmp_path / "src" / "app.py", "def main():\n    return 1\n")
    results = run_checks(tmp_path, all_sources=True)
    assert not [v for r in results for v in r.violations if v.rule == "Forbid foo"]


def test_forbid_regex_flags_line(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-no-tmp\n    category: stack\n    severity: warning\n"
        '    rule: "No tmp vars"\n'
        "    detect:\n      forbid_regex:\n        - 'tmp_\\w+\\s*='\n",
    )
    _write(tmp_path / "src" / "app.py", "tmp_value = 3\nok = 4\n")
    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if v.rule == "No tmp vars"]
    assert len(flagged) == 1
    assert flagged[0].line_number == 1


def test_file_regex_matches_multiline(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-async-route\n    category: stack\n    severity: warning\n"
        '    rule: "Routes must be async"\n'
        "    detect:\n      file_regex:\n"
        "        - '@app\\.get\\([^\\n]*\\)\\s*\\n\\s*def\\s'\n",
    )
    _write(
        tmp_path / "src" / "api.py",
        '@app.get("/x")\ndef handler():\n    return 1\n',
    )
    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if v.rule == "Routes must be async"]
    assert len(flagged) == 1
    assert flagged[0].line_number == 1


def test_disable_comment_suppresses_declared_violation(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-no-foo\n    category: stack\n    severity: error\n"
        '    rule: "Forbid foo"\n'
        "    detect:\n      forbid:\n        - 'foo('\n",
    )
    _write(tmp_path / "src" / "app.py", "foo(1)  # guideline: disable\n")
    results = run_checks(tmp_path, all_sources=True)
    assert not [v for r in results for v in r.violations if v.rule == "Forbid foo"]


def test_match_in_comments_controls_comment_lines(tmp_path: Path) -> None:
    # A neutral token whose prose triggers no built-in phrase detector, so only
    # the declarative detector decides whether comment lines are inspected.
    rule_no_comments = (
        "  - id: py-marker\n    category: stack\n    severity: warning\n"
        '    rule: "Ban inline owner markers"\n'
        "    detect:\n      forbid:\n        - 'OWNER:'\n"
    )
    rule_in_comments = rule_no_comments.replace(
        "        - 'OWNER:'\n", "        - 'OWNER:'\n      match_in_comments: true\n"
    )
    rule_name = "Ban inline owner markers"
    src = "# OWNER: alice\nx = 1\n"

    _referential(tmp_path / "a", rule_no_comments)
    _write(tmp_path / "a" / "src" / "app.py", src)
    assert not [v for r in run_checks(tmp_path / "a", all_sources=True) for v in r.violations if v.rule == rule_name]

    _referential(tmp_path / "b", rule_in_comments)
    _write(tmp_path / "b" / "src" / "app.py", src)
    assert [v for r in run_checks(tmp_path / "b", all_sources=True) for v in r.violations if v.rule == rule_name]


# ─── Drift guard on the shipped Python rules ──────────────────────────────────


def test_shipped_python_rules_carry_detectors() -> None:
    """The three shipped Python stack rules must declare a detector (not silent prose)."""
    instructions = load_yaml_guidelines(_REPO_ROOT)
    declared = {rule for i in instructions for rule in i.rule_detectors}
    for rule_text in (
        "Use Pydantic v2 models exclusively; v1 syntax is forbidden",
        "Define FastAPI route handlers as async def",
        "Emit operational output through the logging module",
    ):
        assert rule_text in declared, f"shipped rule lost its detector: {rule_text!r}"


def test_shipped_pydantic_rule_fires_on_v1_import(tmp_path: Path) -> None:
    """End-to-end proof the (previously silent) pydantic-v2 rule now flags v1 syntax."""
    # Replicate the shipped detector locally so the test stays hermetic.
    _referential(
        tmp_path,
        "  - id: py-pydantic-v2\n    category: stack\n    severity: error\n"
        '    rule: "Use Pydantic v2 models exclusively; v1 syntax is forbidden"\n'
        "    detect:\n      forbid:\n        - 'from pydantic import validator'\n",
    )
    _write(tmp_path / "models.py", "from pydantic import validator\n")
    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if "pydantic" in v.rule.lower()]
    assert flagged
    assert all(v.severity == "error" for v in flagged)
