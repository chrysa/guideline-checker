"""Tests for local autofix — the ``fix:`` block and the fix/`check --fix` surface (L2.1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guideline_checker.autofix import apply_local_fixes
from guideline_checker.checker import RuleResult, Violation
from guideline_checker.cli import main
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines
from guideline_checker.loader import InstructionFile, RuleFix, SourceType

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> Path:
    """A scannable project carrying the real shipped rules and an instruction stub."""
    shutil.copytree(_REPO_ROOT / "guidelines", tmp_path / "guidelines")
    inst = tmp_path / ".github" / "instructions"
    inst.mkdir(parents=True)
    (inst / "r.instructions.md").write_text('---\napplyTo: "**/*.py"\ndescription: r\n---\n- x\n', encoding="utf-8")
    return tmp_path


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ─── Engine unit tests ────────────────────────────────────────────────────────


def _result(file: Path, line: int, content: str, rule: str) -> list[RuleResult]:
    instr = InstructionFile(
        path=Path("x"), apply_to="**/*", description="", content="", source_type=SourceType.GUIDELINES_YAML
    )
    return [
        RuleResult(
            instruction=instr, violations=[Violation(file=file, line_number=line, line_content=content, rule=rule)]
        )
    ]


def test_replace_op_rewrites_line(tmp_path: Path) -> None:
    f = _write(tmp_path, "a.py", "x = yaml.load(t)\n")
    results = _result(f, 1, "x = yaml.load(t)", "no-yaml")
    fixes = {"no-yaml": RuleFix(op="replace", search="yaml.load(", replacement="yaml.safe_load(")}
    report = apply_local_fixes(results, tmp_path, fixes, dry_run=False)
    assert report.fixed_count == 1
    assert f.read_text() == "x = yaml.safe_load(t)\n"


def test_remove_line_op_drops_line(tmp_path: Path) -> None:
    f = _write(tmp_path, "a.py", "a = 1\nbreakpoint()\nb = 2\n")
    results = _result(f, 2, "breakpoint()", "no-debug")
    report = apply_local_fixes(results, tmp_path, {"no-debug": RuleFix(op="remove_line")}, dry_run=False)
    assert report.fixed_count == 1
    assert f.read_text() == "a = 1\nb = 2\n"


def test_regex_replace_op(tmp_path: Path) -> None:
    f = _write(tmp_path, "a.ts", "var n = 1;\n")
    results = _result(f, 1, "var n = 1;", "no-var")
    fixes = {"no-var": RuleFix(op="regex_replace", search=r"\bvar\b", replacement="const")}
    apply_local_fixes(results, tmp_path, fixes, dry_run=False)
    assert f.read_text() == "const n = 1;\n"


def test_dry_run_leaves_disk_untouched_and_emits_diff(tmp_path: Path) -> None:
    original = "x = yaml.load(t)\n"
    f = _write(tmp_path, "a.py", original)
    results = _result(f, 1, "x = yaml.load(t)", "no-yaml")
    fixes = {"no-yaml": RuleFix(op="replace", search="yaml.load(", replacement="yaml.safe_load(")}
    report = apply_local_fixes(results, tmp_path, fixes, dry_run=True)
    assert f.read_text() == original  # untouched
    assert "yaml.safe_load(" in report.diff and report.diff.startswith("---")


def test_violation_without_a_fix_is_left_alone(tmp_path: Path) -> None:
    f = _write(tmp_path, "a.py", "eval(x)\n")
    results = _result(f, 1, "eval(x)", "no-eval")
    report = apply_local_fixes(results, tmp_path, {}, dry_run=False)  # no fix registered
    assert report.fixed_count == 0
    assert f.read_text() == "eval(x)\n"


# ─── End-to-end via the CLI against the real shipped fixes ────────────────────


def test_fix_subcommand_applies_all_ops(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write(root, "src/a.py", "import yaml\nx = yaml.load(t)\nbreakpoint()\n")
    _write(root, "src/b.ts", "var n = 1;\n")
    code = main(["fix", "--root", str(root)])
    assert code == 0
    assert root.joinpath("src/a.py").read_text() == "import yaml\nx = yaml.safe_load(t)\n"
    assert root.joinpath("src/b.ts").read_text() == "const n = 1;\n"


def test_fix_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src = _write(root, "src/a.py", "x = yaml.load(t)\n")
    main(["fix", "--root", str(root)])
    once = src.read_text()
    main(["fix", "--root", str(root)])
    assert src.read_text() == once  # second run changes nothing


def test_check_fix_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    original = "x = yaml.load(t)\n"
    src = _write(root, "src/a.py", original)
    code = main(["check", "--root", str(root), "--output", str(tmp_path / "r.html"), "--fix", "--dry-run"])
    assert code == 0
    assert src.read_text() == original


def test_detect_only_rule_is_not_fixed_via_cli(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src = _write(root, "src/a.py", "eval(payload)\n")  # py-no-eval-exec has no fix: block
    main(["fix", "--root", str(root)])
    assert src.read_text() == "eval(payload)\n"


# ─── Loader validation of the fix: block ──────────────────────────────────────

_CATS = "categories:\n  - id: correctness\n    description: x\n"


def _ref(root: Path, rule_yaml: str) -> None:
    (root / "guidelines").mkdir()
    (root / "guidelines" / "categories.yml").write_text(_CATS, encoding="utf-8")
    (root / "guidelines" / "languages").mkdir()
    (root / "guidelines" / "languages" / "python.yml").write_text(
        f'language_target: python\napply_to_glob: "**/*.py"\nrules:\n{rule_yaml}', encoding="utf-8"
    )


def test_valid_fix_block_parses(tmp_path: Path) -> None:
    _ref(
        tmp_path,
        "  - id: r1\n    category: correctness\n    severity: warning\n    rule: no debug\n"
        "    detect:\n      forbid: ['breakpoint(']\n    fix:\n      op: remove_line\n",
    )
    instrs = load_yaml_guidelines(tmp_path)
    fixes = {r: f for i in instrs for r, f in i.rule_fixes.items()}
    assert fixes["no debug"].op == "remove_line"


@pytest.mark.parametrize(
    "fix_yaml",
    [
        "    fix:\n      op: bogus\n",  # unknown op
        "    fix:\n      op: replace\n      to: y\n",  # replace missing 'from'
        "    fix:\n      op: regex_replace\n      pattern: p\n",  # missing 'replacement'
        "    fix: not-a-mapping\n",  # wrong type
    ],
)
def test_invalid_fix_block_raises(tmp_path: Path, fix_yaml: str) -> None:
    _ref(
        tmp_path,
        f"  - id: r1\n    category: correctness\n    severity: warning\n    rule: r\n"
        f"    detect:\n      forbid: ['x']\n{fix_yaml}",
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)
