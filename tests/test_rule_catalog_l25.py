"""Catalog coverage for the L2.5 rule-library expansion.

Every new shipped rule is proven end-to-end against the *real* shipped
``guidelines/`` tree: a positive fixture must raise exactly one violation of the
rule, and a clean fixture must raise none. A completeness guard asserts the
detected-rule count has at least doubled and that no language rule ships without
a detector (the D-0004 / D-0006 dead-rule anti-pattern).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from guideline_checker.checker import run_checks
from guideline_checker.guidelines import load_yaml_guidelines

_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Case:
    rule_text: str
    filename: str
    violating: str
    clean: str


# One case per rule added in L2.5. `violating` must trip exactly the target rule;
# `clean` must trip none of it. Files are chosen so only the target dimension applies
# (.py → python, .ts → typescript, .jsx → react; all get the transverse _common rules).
_CASES = [
    # ── Python ────────────────────────────────────────────────────────────────
    Case("Catch specific exceptions, never a bare except", "src/m.py", "try:\n    f()\nexcept:\n    pass\n", "x = 1\n"),
    Case("Import names explicitly, never with a wildcard star import", "src/m.py", "from os import *\n", "import os\n"),
    Case("Never use eval() or exec() on runtime data", "src/m.py", "eval(data)\n", "x = 1\n"),
    Case("Never spawn a subprocess with shell=True", "src/m.py", "run(cmd, shell=True)\n", "run(cmd)\n"),
    Case("Use the subprocess module instead of os.system()", "src/m.py", "os.system(cmd)\n", "x = 1\n"),
    Case(
        "Parse YAML with safe_load, never the unsafe yaml.load", "src/m.py", "yaml.load(txt)\n", "yaml.safe_load(txt)\n"
    ),
    Case("Remove debugger entry points before committing", "src/m.py", "breakpoint()\n", "x = 1\n"),
    # ── TypeScript ────────────────────────────────────────────────────────────
    Case("Use a logger instead of console.log or console.debug", "src/m.ts", "console.log(x);\n", "logger.info(x);\n"),
    Case("Declare bindings with const or let, never var", "src/m.ts", "var x = 1;\n", "const x = 1;\n"),
    Case("Remove debugger statements before committing", "src/m.ts", "debugger;\n", "const x = 1;\n"),
    Case("Never use eval() on runtime data in TypeScript", "src/m.ts", "eval(src);\n", "const x = 1;\n"),
    # ── React ─────────────────────────────────────────────────────────────────
    Case(
        "Avoid dangerouslySetInnerHTML; sanitise and render content safely",
        "src/c.jsx",
        "const A = () => <div dangerouslySetInnerHTML={{__html: s}} />;\n",
        "const A = () => <div />;\n",
    ),
    Case(
        "Use a ref instead of the deprecated findDOMNode",
        "src/c.jsx",
        "findDOMNode(node);\n",
        "const A = () => <div />;\n",
    ),
    # ── Transverse (_common) ──────────────────────────────────────────────────
    Case("Resolve FIXME markers before merging", "src/m.py", "# FIXME broken\nx = 1\n", "x = 1\n"),
]


def _project(tmp_path: Path) -> Path:
    """A scannable project rooted at tmp_path carrying the real shipped rules."""
    shutil.copytree(_REPO_ROOT / "guidelines", tmp_path / "guidelines")
    return tmp_path


def _violations_of(root: Path, rule_text: str) -> list[object]:
    results = run_checks(root, all_sources=True)
    return [v for r in results for v in r.violations if v.rule == rule_text]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.filename + ":" + c.rule_text[:24])
def test_new_rule_fires_on_violation(case: Case, tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / case.filename).parent.mkdir(parents=True, exist_ok=True)
    (root / case.filename).write_text(case.violating, encoding="utf-8")
    assert len(_violations_of(root, case.rule_text)) == 1


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.filename + ":" + c.rule_text[:24])
def test_new_rule_clean_file_is_silent(case: Case, tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / case.filename).parent.mkdir(parents=True, exist_ok=True)
    (root / case.filename).write_text(case.clean, encoding="utf-8")
    assert _violations_of(root, case.rule_text) == []


def test_detected_rule_count_at_least_doubled() -> None:
    """L2.5 goal: the count of detected rules is >= 24 (doubled from the pre-L2.5 12)."""
    instructions = load_yaml_guidelines(_REPO_ROOT)
    detected = {rule for i in instructions for rule in i.rule_detectors}
    assert len(detected) >= 24


def test_no_language_rule_ships_without_a_detector() -> None:
    """Every rule under guidelines/languages/ must carry a detector — no dead prose rules."""
    for instruction in load_yaml_guidelines(_REPO_ROOT):
        if "languages" not in instruction.path.parts:
            continue
        undetected = set(instruction.rules) - set(instruction.rule_detectors)
        assert not undetected, f"{instruction.path} has dead rules: {undetected}"
