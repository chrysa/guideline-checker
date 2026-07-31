"""``detect.exclude``: one rule opting out of paths its file-level glob covers.

``apply_to_glob`` is declared once per referential file, so every rule in it sees
the same files. That blocked ``py-no-assert-as-validation`` for a whole release:
``assert`` is a defect in a runtime guard and the entire point of a test, and one
glob cannot say both.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.checker import run_checks

_CATEGORIES = "categories:\n  - id: correctness\n    description: x\n"

_RULE = "Do not use assert to guard runtime behaviour outside tests"


def _referential(root: Path, *, exclude: str) -> None:
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages" / "python.yml").write_text(
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-no-assert\n    category: correctness\n    severity: warning\n"
        f'    rule: "{_RULE}"\n'
        f"    detect:\n      ast:\n        - assert-as-validation\n{exclude}",
        encoding="utf-8",
    )


def _flagged(root: Path) -> list[str]:
    return [v.file.name for result in run_checks(root, all_sources=True) for v in result.violations if v.rule == _RULE]


def _project(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "src" / "guard.py").write_text("def check(v):\n    assert v > 0\n", encoding="utf-8")
    (root / "tests" / "test_guard.py").write_text("def test_it():\n    assert True\n", encoding="utf-8")


def test_a_runtime_guard_is_flagged(tmp_path: Path) -> None:
    _referential(tmp_path, exclude="      exclude:\n        - tests\n")
    _project(tmp_path)

    assert _flagged(tmp_path) == ["guard.py"]


def test_a_test_file_is_left_alone(tmp_path: Path) -> None:
    """The whole reason the rule could not ship."""
    _referential(tmp_path, exclude="      exclude:\n        - tests\n")
    _project(tmp_path)

    assert "test_guard.py" not in _flagged(tmp_path)


def test_without_the_exclusion_the_rule_floods(tmp_path: Path) -> None:
    """Discrimination proof: the guard is doing the work, not the host's ignore file.

    This repo lists ``tests`` in .guidelineignore, so a self-scan would look clean
    either way — and shipping on that evidence would flood every consumer that
    does not.
    """
    _referential(tmp_path, exclude="")
    _project(tmp_path)

    assert sorted(_flagged(tmp_path)) == ["guard.py", "test_guard.py"]


def test_a_glob_pattern_excludes_too(tmp_path: Path) -> None:
    """Not every project keeps its tests in a directory called tests/."""
    _referential(tmp_path, exclude="      exclude:\n        - '**/test_*.py'\n")
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "test_helpers.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (tmp_path / "src" / "guard.py").write_text("def check(v):\n    assert v > 0\n", encoding="utf-8")

    assert _flagged(tmp_path) == ["guard.py"]
