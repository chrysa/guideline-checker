"""Tests for the AST-backed Python detectors and their ``detect.ast`` wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.ast_python import VALID_AST_CHECKS, run_ast_checks, unknown_checks
from guideline_checker.checker import run_checks
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines

# ─── pydantic-v1 ──────────────────────────────────────────────────────────────


def test_pydantic_v1_flags_real_import() -> None:
    found = run_ast_checks(["pydantic-v1"], "from pydantic import validator\n")
    assert [lineno for lineno, _ in found] == [1]


def test_pydantic_v1_flags_validator_decorator() -> None:
    src = (
        "from pydantic import BaseModel\n\n\n"
        "class M(BaseModel):\n"
        "    @validator('x')\n"
        "    def check(cls, v):\n"
        "        return v\n"
    )
    found = run_ast_checks(["pydantic-v1"], src)
    assert any("decorator" in msg for _, msg in found)


def test_pydantic_v1_ignores_string_and_comment() -> None:
    # The whole point of AST over substring: text inside data is not a violation.
    src = 's = "from pydantic import validator"\n# from pydantic import validator\nx = 1\n'
    assert run_ast_checks(["pydantic-v1"], src) == []


def test_pydantic_v1_ignores_v2_basemodel() -> None:
    assert run_ast_checks(["pydantic-v1"], "from pydantic import BaseModel, Field\n") == []


def test_pydantic_v1_flags_explicit_v1_module() -> None:
    assert run_ast_checks(["pydantic-v1"], "from pydantic.v1 import BaseModel\n")
    assert run_ast_checks(["pydantic-v1"], "from pydantic.class_validators import validator\n")


def test_pydantic_v1_dotted_decorator_ignored() -> None:
    # A dotted decorator like @app.get must not be mistaken for @validator.
    src = '@app.get("/x")\ndef h():\n    return 1\n'
    assert run_ast_checks(["pydantic-v1"], src) == []


# ─── sync-fastapi-route ───────────────────────────────────────────────────────


def test_sync_route_flagged() -> None:
    src = '@app.get("/x")\ndef handler():\n    return 1\n'
    found = run_ast_checks(["sync-fastapi-route"], src)
    assert [lineno for lineno, _ in found] == [1]


def test_async_route_not_flagged() -> None:
    src = '@app.get("/x")\nasync def handler():\n    return 1\n'
    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_router_and_multiline_decorator_flagged() -> None:
    src = '@router.post(\n    "/items",\n    status_code=201,\n)\ndef create():\n    return 1\n'
    found = run_ast_checks(["sync-fastapi-route"], src)
    assert found and found[0][0] == 1  # reported at the decorator line


def test_non_route_decorator_ignored() -> None:
    src = "@staticmethod\ndef helper():\n    return 1\n"
    assert run_ast_checks(["sync-fastapi-route"], src) == []


def test_attribute_owner_route_flagged() -> None:
    # Owner reached through an attribute chain: @self.router.post(...).
    src = '@self.router.post("/x")\ndef create():\n    return 1\n'
    assert run_ast_checks(["sync-fastapi-route"], src)


def test_bare_and_unknown_owner_decorators_ignored() -> None:
    # Bare name decorator (not a Call) and a get() on an unrelated owner.
    src = "@cached\ndef a():\n    return 1\n\n\n@thing.get()\ndef b():\n    return 2\n"
    assert run_ast_checks(["sync-fastapi-route"], src) == []


# ─── mutable-default-arg ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "default",
    ["[]", "{}", "{1, 2}", "list()", "dict()", "set()"],
)
def test_mutable_default_flagged(default: str) -> None:
    src = f"def f(x={default}):\n    return x\n"
    found = run_ast_checks(["mutable-default-arg"], src)
    assert [lineno for lineno, _ in found] == [1]


def test_mutable_default_keyword_only_and_async() -> None:
    src = "async def f(*, items=[]):\n    return items\n"
    assert run_ast_checks(["mutable-default-arg"], src)


@pytest.mark.parametrize(
    "default",
    ["None", "5", "'s'", "()", "(1, 2)", "frozenset()", "MY_CONST"],
)
def test_immutable_default_ignored(default: str) -> None:
    src = f"def f(x={default}):\n    return x\n"
    assert run_ast_checks(["mutable-default-arg"], src) == []


# ─── run_ast_checks behaviour ─────────────────────────────────────────────────


def test_syntax_error_yields_no_findings() -> None:
    assert run_ast_checks(["pydantic-v1"], "def (((\n") == []


def test_unknown_name_ignored_at_runtime() -> None:
    assert run_ast_checks(["does-not-exist"], "from pydantic import validator\n") == []


def test_unknown_checks_helper() -> None:
    assert unknown_checks(["pydantic-v1", "nope"]) == ["nope"]
    assert set(VALID_AST_CHECKS) == {
        "pydantic-v1",
        "sync-fastapi-route",
        "mutable-default-arg",
        "silent-exception",
        "assert-as-validation",
    }


# ─── YAML wiring + end-to-end ─────────────────────────────────────────────────

_CATEGORIES = "categories:\n  - id: stack\n    description: x\n"


def _referential(root: Path, rule_yaml: str) -> None:
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages" / "python.yml").write_text(
        f'language_target: python\napply_to_glob: "**/*.py"\nrules:\n{rule_yaml}', encoding="utf-8"
    )


def test_detect_ast_populates_rule_detector(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-x\n    category: stack\n    severity: error\n"
        '    rule: "No pydantic v1"\n    detect:\n      ast:\n        - pydantic-v1\n',
    )
    instructions = load_yaml_guidelines(tmp_path)
    detectors = {r: d for i in instructions for r, d in i.rule_detectors.items()}
    assert detectors["No pydantic v1"].ast_checks == ("pydantic-v1",)


def test_unknown_ast_check_rejected(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-x\n    category: stack\n    severity: error\n"
        '    rule: "bad"\n    detect:\n      ast:\n        - made-up-check\n',
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


def test_ast_detector_end_to_end_with_severity(tmp_path: Path) -> None:
    _referential(
        tmp_path,
        "  - id: py-pydantic-v2\n    category: stack\n    severity: error\n"
        '    rule: "No pydantic v1"\n    detect:\n      ast:\n        - pydantic-v1\n',
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "models.py").write_text("from pydantic import validator\n", encoding="utf-8")
    (tmp_path / "src" / "ok.py").write_text('x = "from pydantic import validator"\n', encoding="utf-8")

    results = run_checks(tmp_path, all_sources=True)
    flagged = [v for r in results for v in r.violations if v.rule == "No pydantic v1"]
    assert [str(v.file) for v in flagged] == [str(tmp_path / "src" / "models.py")]  # ok.py not flagged
    assert all(v.severity == "error" for v in flagged)


def test_shipped_python_rules_use_ast(tmp_path: Path) -> None:
    """Drift guard: the shipped pydantic/async rules carry AST checks."""
    repo_root = Path(__file__).resolve().parents[1]
    instructions = load_yaml_guidelines(repo_root)
    ast_by_rule = {r: d.ast_checks for i in instructions for r, d in i.rule_detectors.items()}
    assert ast_by_rule.get("Use Pydantic v2 models exclusively; v1 syntax is forbidden") == ("pydantic-v1",)
    assert ast_by_rule.get("Define FastAPI route handlers as async def") == ("sync-fastapi-route",)


# ─── silent-exception ─────────────────────────────────────────────────────────


def test_silent_exception_flags_a_blanket_catch_that_does_nothing() -> None:
    src = "def run():\n    try:\n        go()\n    except Exception:\n        pass\n"
    found = run_ast_checks(["silent-exception"], src)
    assert [lineno for lineno, _ in found] == [4]


def test_silent_exception_flags_a_bare_except() -> None:
    src = "def run():\n    try:\n        go()\n    except:\n        pass\n"
    assert run_ast_checks(["silent-exception"], src)


def test_silent_exception_leaves_a_narrow_handler_alone() -> None:
    """Catching precisely and acting on it is correct — no false alarm."""
    src = "def run():\n    try:\n        go()\n    except ValueError:\n        return None\n"
    assert run_ast_checks(["silent-exception"], src) == []


def test_silent_exception_leaves_a_blanket_catch_that_acts_alone() -> None:
    """What makes it a defect is the silence, not the breadth."""
    src = "def run():\n    try:\n        go()\n    except Exception:\n        logger.exception('failed')\n"
    assert run_ast_checks(["silent-exception"], src) == []


# ─── assert-as-validation ─────────────────────────────────────────────────────


def test_assert_as_validation_flags_a_runtime_guard() -> None:
    found = run_ast_checks(["assert-as-validation"], "def check(v):\n    assert v > 0\n")
    assert [lineno for lineno, _ in found] == [2]


def test_assert_as_validation_explains_why_it_matters() -> None:
    _lineno, message = run_ast_checks(["assert-as-validation"], "assert x\n")[0]
    assert "-O" in message


def test_assert_as_validation_is_silent_without_asserts() -> None:
    assert run_ast_checks(["assert-as-validation"], "def check(v):\n    return v > 0\n") == []
