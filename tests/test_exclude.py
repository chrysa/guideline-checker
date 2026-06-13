"""Tests for the ``--exclude`` scan-scoping feature (run_checks exclude + CLI flag)."""

from __future__ import annotations

from pathlib import Path

from guideline_checker import cli
from guideline_checker.checker import _is_excluded, _read_ignore_file, run_checks

_CATEGORIES = """\
categories:
  - id: stack
    description: "Stack choices"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path) -> None:
    """A project with a declarative forbid rule and two violating .py files."""
    _write(root / "guidelines" / "categories.yml", _CATEGORIES)
    _write(
        root / "guidelines" / "languages" / "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: py-no-foo\n    category: stack\n    severity: error\n"
        '    rule: "Forbid foo"\n'
        "    detect:\n      forbid:\n        - 'foo('\n",
    )
    _write(root / "src" / "app.py", "foo(1)\n")
    _write(root / "tests" / "test_app.py", "foo(2)\n")


def _foo_violations(root: Path, **kwargs: object) -> list[str]:
    results = run_checks(root, all_sources=True, **kwargs)  # type: ignore[arg-type]
    return [str(v.file) for r in results for v in r.violations if v.rule == "Forbid foo"]


# ─── _is_excluded semantics ───────────────────────────────────────────────────


def test_bare_directory_excludes_everything_beneath(tmp_path: Path) -> None:
    f = tmp_path / "tests" / "a" / "b.py"
    assert _is_excluded(f, tmp_path, ["tests"]) is True


def test_recursive_glob_excludes_nested(tmp_path: Path) -> None:
    f = tmp_path / "scripts" / "deep" / "x.py"
    assert _is_excluded(f, tmp_path, ["scripts/**/*.py"]) is True
    assert _is_excluded(tmp_path / "src" / "x.py", tmp_path, ["scripts/**/*.py"]) is False


def test_suffix_glob_and_no_false_prefix(tmp_path: Path) -> None:
    assert _is_excluded(tmp_path / "docs" / "n.md", tmp_path, ["**/*.md"]) is True
    # "test" must not exclude "tests/..." by accidental prefix.
    assert _is_excluded(tmp_path / "tests" / "x.py", tmp_path, ["test"]) is False


def test_file_outside_root_not_excluded(tmp_path: Path) -> None:
    assert _is_excluded(Path("/elsewhere/x.py"), tmp_path, ["**/*.py"]) is False


# ─── run_checks(exclude=...) end-to-end ───────────────────────────────────────


def test_no_exclude_flags_both_files(tmp_path: Path) -> None:
    _project(tmp_path)
    assert len(_foo_violations(tmp_path)) == 2


def test_exclude_bare_dir_skips_tests(tmp_path: Path) -> None:
    _project(tmp_path)
    flagged = _foo_violations(tmp_path, exclude=["tests"])
    assert len(flagged) == 1
    assert all("tests" not in f for f in flagged)


def test_exclude_comma_separated_single_value(tmp_path: Path) -> None:
    _project(tmp_path)
    assert _foo_violations(tmp_path, exclude=["tests, src"]) == []


def test_exclude_none_is_noop(tmp_path: Path) -> None:
    _project(tmp_path)
    assert len(_foo_violations(tmp_path, exclude=None)) == 2


# ─── CLI flag wiring ──────────────────────────────────────────────────────────


def test_cli_accepts_repeated_exclude(tmp_path: Path) -> None:
    _project(tmp_path)
    code = cli.main(
        [
            "check",
            "--root",
            str(tmp_path),
            "--output",
            str(tmp_path / "r.html"),
            "--exclude",
            "tests",
            "--exclude",
            "src",
            "--fail-on",
            "error",
        ]
    )
    # Both violating files excluded → no error-level violations → exit 0.
    assert code == 0


# ─── .guidelineignore file ────────────────────────────────────────────────────


def test_read_ignore_file_absent_is_empty(tmp_path: Path) -> None:
    assert _read_ignore_file(tmp_path) == []


def test_read_ignore_file_skips_comments_and_blanks(tmp_path: Path) -> None:
    _write(tmp_path / ".guidelineignore", "# a comment\n\ntests\n  scripts/**  \n")
    assert _read_ignore_file(tmp_path) == ["tests", "scripts/**"]


def test_guidelineignore_excludes_files(tmp_path: Path) -> None:
    _project(tmp_path)
    _write(tmp_path / ".guidelineignore", "# skip the test tree\ntests\n")
    flagged = _foo_violations(tmp_path)
    assert len(flagged) == 1
    assert all("tests" not in f for f in flagged)


def test_guidelineignore_and_exclude_arg_combine(tmp_path: Path) -> None:
    _project(tmp_path)
    _write(tmp_path / ".guidelineignore", "tests\n")
    # File ignores tests/, the arg ignores src/ → both violating files skipped.
    assert _foo_violations(tmp_path, exclude=["src"]) == []
