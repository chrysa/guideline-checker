"""Tests for the tree-sitter JS/TS detectors and their ``detect.ast`` wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.ast_javascript import (
    VALID_JS_AST_CHECKS,
    run_js_ast_checks,
    unknown_js_checks,
)
from guideline_checker.ast_python import VALID_AST_CHECKS
from guideline_checker.checker import run_checks
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines

# ─── ts-any-type ──────────────────────────────────────────────────────────────


def test_any_annotation_flagged() -> None:
    found = run_js_ast_checks(["ts-any-type"], "const x: any = 1;\n", ".ts")
    assert [lineno for lineno, _ in found] == [1]


def test_as_any_flagged() -> None:
    found = run_js_ast_checks(["ts-any-type"], "const y = z as any;\n", ".ts")
    assert [lineno for lineno, _ in found] == [1]


def test_any_inside_string_ignored() -> None:
    # The point of AST over substring: "any" as data is not a type.
    assert run_js_ast_checks(["ts-any-type"], 'const s = "any";\n', ".ts") == []


def test_precise_type_not_flagged() -> None:
    assert run_js_ast_checks(["ts-any-type"], "const n: number = 1;\n", ".ts") == []


# ─── ts-suppression ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("comment", ["// @ts-ignore", "// @ts-nocheck", "/* @ts-ignore */"])
def test_suppression_comment_flagged(comment: str) -> None:
    found = run_js_ast_checks(["ts-suppression"], f"{comment}\nconst x: number = 1;\n", ".ts")
    assert [lineno for lineno, _ in found] == [1]


def test_expect_error_not_flagged() -> None:
    # @ts-expect-error self-clears when no error exists, so it is not a silent suppression.
    assert run_js_ast_checks(["ts-suppression"], "// @ts-expect-error\nconst x = 1;\n", ".ts") == []


def test_plain_comment_not_flagged() -> None:
    assert run_js_ast_checks(["ts-suppression"], "// just a note\nconst x = 1;\n", ".ts") == []


# ─── ts-non-null-assertion ────────────────────────────────────────────────────


def test_non_null_assertion_flagged() -> None:
    found = run_js_ast_checks(["ts-non-null-assertion"], "const v = x!;\n", ".ts")
    assert [lineno for lineno, _ in found] == [1]


def test_non_null_assertion_member_access_flagged() -> None:
    found = run_js_ast_checks(["ts-non-null-assertion"], "const v = obj!.prop;\n", ".ts")
    assert [lineno for lineno, _ in found] == [1]


def test_negation_not_flagged() -> None:
    # `!x` is unary negation, not a non-null assertion.
    assert run_js_ast_checks(["ts-non-null-assertion"], "const v = !x;\n", ".ts") == []


def test_inequality_not_flagged() -> None:
    assert run_js_ast_checks(["ts-non-null-assertion"], "const v = a !== b;\n", ".ts") == []


# ─── react-hook-order ─────────────────────────────────────────────────────────


def test_conditional_hook_flagged() -> None:
    src = "function C() {\n  if (cond) {\n    const [s, set] = useState(0);\n  }\n  return <div />;\n}\n"
    found = run_js_ast_checks(["react-hook-order"], src, ".tsx")
    assert [lineno for lineno, _ in found] == [3]


def test_short_circuit_hook_flagged() -> None:
    src = "function C() {\n  return <div>{flag && useMemo(() => 1)}</div>;\n}\n"
    found = run_js_ast_checks(["react-hook-order"], src, ".tsx")
    assert [lineno for lineno, _ in found] == [2]


def test_top_level_hook_not_flagged() -> None:
    src = "function C() {\n  const [s, set] = useState(0);\n  return <div />;\n}\n"
    assert run_js_ast_checks(["react-hook-order"], src, ".tsx") == []


def test_non_hook_call_in_condition_ignored() -> None:
    src = "function C() {\n  if (cond) {\n    doThing();\n  }\n  return <div />;\n}\n"
    assert run_js_ast_checks(["react-hook-order"], src, ".tsx") == []


# ─── react-index-key ──────────────────────────────────────────────────────────


def test_index_key_flagged() -> None:
    src = (
        "function List({ items }) {\n"
        "  return (\n"
        "    <ul>\n"
        "      {items.map((item, i) => (\n"
        "        <li key={i}>{item}</li>\n"
        "      ))}\n"
        "    </ul>\n"
        "  );\n"
        "}\n"
    )
    found = run_js_ast_checks(["react-index-key"], src, ".tsx")
    assert [lineno for lineno, _ in found] == [5]


def test_stable_key_not_flagged() -> None:
    src = (
        "function List({ items }) {\n  return <ul>{items.map((item) => <li key={item.id}>{item.name}</li>)}</ul>;\n}\n"
    )
    assert run_js_ast_checks(["react-index-key"], src, ".tsx") == []


def test_identifier_key_outside_map_not_flagged() -> None:
    src = "function C({ i }) {\n  return <li key={i}>x</li>;\n}\n"
    assert run_js_ast_checks(["react-index-key"], src, ".tsx") == []


# ─── react-inline-component ───────────────────────────────────────────────────


def test_inline_component_flagged() -> None:
    src = "function Outer() {\n  const Inner = () => <span />;\n  return <Inner />;\n}\n"
    found = run_js_ast_checks(["react-inline-component"], src, ".tsx")
    assert [lineno for lineno, _ in found] == [2]


def test_module_scope_component_not_flagged() -> None:
    src = "const Inner = () => <span />;\nfunction Outer() {\n  return <Inner />;\n}\n"
    assert run_js_ast_checks(["react-inline-component"], src, ".tsx") == []


def test_lowercase_render_helper_not_flagged() -> None:
    # A lowercase helper returning JSX is not a *component* definition.
    src = "function Outer() {\n  const renderRow = () => <td />;\n  return <table>{renderRow()}</table>;\n}\n"
    assert run_js_ast_checks(["react-inline-component"], src, ".tsx") == []


# ─── react-missing-effect-deps ────────────────────────────────────────────────


@pytest.mark.parametrize("hook", ["useEffect", "useLayoutEffect", "useMemo", "useCallback"])
def test_missing_effect_deps_flagged(hook: str) -> None:
    found = run_js_ast_checks(["react-missing-effect-deps"], f"{hook}(() => doThing());\n", ".tsx")
    assert [lineno for lineno, _ in found] == [1]


def test_effect_with_deps_not_flagged() -> None:
    assert run_js_ast_checks(["react-missing-effect-deps"], "useEffect(() => doThing(), [x]);\n", ".tsx") == []


def test_effect_empty_deps_array_not_flagged() -> None:
    # An explicit [] is a deliberate run-once; only the *missing* argument is the smell.
    assert run_js_ast_checks(["react-missing-effect-deps"], "useEffect(() => doThing(), []);\n", ".tsx") == []


def test_non_hook_single_arg_call_ignored() -> None:
    assert run_js_ast_checks(["react-missing-effect-deps"], "subscribe(() => doThing());\n", ".tsx") == []


# ─── run_js_ast_checks behaviour ──────────────────────────────────────────────


def test_malformed_source_does_not_crash() -> None:
    # tree-sitter produces an ERROR tree rather than raising; detection must not crash.
    result = run_js_ast_checks(["ts-suppression"], "function (((( @ts-ignore\n", ".ts")
    assert isinstance(result, list)


def test_unsupported_suffix_yields_nothing() -> None:
    assert run_js_ast_checks(["ts-any-type"], "const x: any = 1;\n", ".py") == []


def test_unknown_name_ignored_at_runtime() -> None:
    assert run_js_ast_checks(["does-not-exist"], "const x: any = 1;\n", ".ts") == []


def test_unknown_js_checks_helper() -> None:
    assert unknown_js_checks(["ts-any-type", "nope"]) == ["nope"]
    assert set(VALID_JS_AST_CHECKS) == {
        "ts-any-type",
        "ts-suppression",
        "ts-non-null-assertion",
        "react-hook-order",
        "react-index-key",
        "react-inline-component",
        "react-missing-effect-deps",
    }


def test_python_and_js_check_names_are_disjoint() -> None:
    assert VALID_AST_CHECKS.isdisjoint(VALID_JS_AST_CHECKS)


# ─── YAML wiring + end-to-end ─────────────────────────────────────────────────

_CATEGORIES = "categories:\n  - id: stack\n    description: x\n"


def _referential(root: Path, rule_yaml: str) -> None:
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages" / "react.yml").write_text(
        f'language_target: react\napply_to_glob: "**/*.tsx,**/*.jsx"\nrules:\n{rule_yaml}',
        encoding="utf-8",
    )


def test_detect_ast_populates_js_rule_detector(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: react-x\n    category: stack\n    severity: error\n"
        '    rule: "No conditional hooks"\n    detect:\n      ast:\n        - react-hook-order\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {r: d for i in instructions for r, d in i.rule_detectors.items()}
    assert detectors["No conditional hooks"].ast_checks == ("react-hook-order",)


def test_unknown_js_ast_check_rejected(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: react-x\n    category: stack\n    severity: error\n"
        '    rule: "bad"\n    detect:\n      ast:\n        - made-up-js-check\n',
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


def test_js_ast_detector_end_to_end_with_severity(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: react-hooks-top-level\n    category: stack\n    severity: error\n"
        '    rule: "Call hooks only at the top level"\n    detect:\n      ast:\n        - react-hook-order\n',
    )
    (tmp_path / "src").mkdir()
    bad = "function C() {\n  if (x) {\n    const [s] = useState(0);\n  }\n  return <div />;\n}\n"
    ok = "function C() {\n  const [s] = useState(0);\n  return <div />;\n}\n"
    (tmp_path / "src" / "Bad.tsx").write_text(bad, encoding="utf-8")
    (tmp_path / "src" / "Ok.tsx").write_text(ok, encoding="utf-8")

    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if v.rule == "Call hooks only at the top level"]
    assert [str(v.file) for v in flagged] == [str(tmp_path / "src" / "Bad.tsx")]  # Ok.tsx clean
    assert all(v.severity == "error" for v in flagged)


def test_shipped_js_rules_use_ast(tmp_path: Path) -> None:
    """Drift guard: the shipped TS/React rules carry their AST checks."""
    repo_root = Path(__file__).resolve().parents[1]
    instructions = load_yaml_guidelines(repo_root)
    ast_by_rule = {r: d.ast_checks for i in instructions for r, d in i.rule_detectors.items()}
    assert ast_by_rule.get("Prefer precise types over the any escape hatch") == ("ts-any-type",)
    assert ast_by_rule.get("Resolve type errors instead of suppressing them") == ("ts-suppression",)
    assert ast_by_rule.get(
        "Prove non-null with a check instead of asserting it with the non-null operator",
    ) == ("ts-non-null-assertion",)
    assert ast_by_rule.get("Key dynamic lists by a stable identity, never by array index") == ("react-index-key",)
    assert ast_by_rule.get(
        "Pass a dependency array to useEffect, useLayoutEffect, useMemo, and useCallback",
    ) == ("react-missing-effect-deps",)


def test_mutation_without_feedback_flags_bare_options() -> None:
    src = "const m = useMutation({ mutationFn: save });\n"
    assert len(run_js_ast_checks(["mutation-without-feedback"], src, ".tsx")) == 1


def test_mutation_without_feedback_passes_onerror() -> None:
    src = "const m = useMutation({ mutationFn: save, onError: notify });\n"
    assert run_js_ast_checks(["mutation-without-feedback"], src, ".tsx") == []


def test_mutation_without_feedback_passes_onsettled() -> None:
    src = "const m = useMutation({ mutationFn: save, onSettled: done });\n"
    assert run_js_ast_checks(["mutation-without-feedback"], src, ".tsx") == []


def test_mutation_without_feedback_ignores_off_object_options() -> None:
    src = "const m = useMutation(opts);\n"
    assert run_js_ast_checks(["mutation-without-feedback"], src, ".tsx") == []
