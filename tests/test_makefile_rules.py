"""The shipped Makefile referential, proven against real build files.

The Makefile is where the gates live, and it was the one surface no rule covered.
Every defect found in the build-surface audit — a stale-image test target, a guard
that could never fire, a documented target that did not exist — sat in a file the
tool did not read.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guideline_checker.checker import run_checks

REFERENTIAL = Path(__file__).resolve().parents[1] / "guidelines"

SOCLE = (
    "help",
    "install",
    "install-dev",
    "lint",
    "format",
    "format-check",
    "typecheck",
    "test",
    "test-cov",
    "pre-commit",
    "clean",
    "ci",
    "quality-gate-baseline",
    "quality-gate-verify",
)

COMPLETE = "".join(f"{name}: ## does something\n\t@true\n" for name in SOCLE)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project carrying the shipped referential.

    The referential is read from the *scanned root*, not the installed package —
    without this copy the fixture silently checks nothing.
    """
    shutil.copytree(REFERENTIAL, tmp_path / "guidelines")
    return tmp_path


def _violations(root: Path) -> list[str]:
    return [
        v.line_content
        for result in run_checks(root, all_sources=True)
        for v in result.violations
        if v.file.name == "Makefile"
    ]


def test_a_complete_makefile_passes(project: Path) -> None:
    (project / "Makefile").write_text(COMPLETE, encoding="utf-8")
    assert _violations(project) == []


def test_a_missing_target_is_reported_by_name(project: Path) -> None:
    without_typecheck = COMPLETE.replace("typecheck: ## does something\n\t@true\n", "")
    (project / "Makefile").write_text(without_typecheck, encoding="utf-8")

    found = _violations(project)

    assert len(found) == 1
    assert "typecheck" in found[0]  # the finding names what is missing


def test_every_missing_target_is_reported_separately(project: Path) -> None:
    """An empty Makefile is missing all fourteen, not "a socle"."""
    (project / "Makefile").write_text("# nothing here\n", encoding="utf-8")
    assert len(_violations(project)) == len(SOCLE)


def test_a_renamed_target_does_not_satisfy_the_socle(project: Path) -> None:
    """``type-check`` is the exact rename the standard calls out — it must not pass.

    It is both a missing ``typecheck`` and a banned spelling, so it fires twice.
    """
    renamed = COMPLETE.replace("typecheck: ## does something", "type-check: ## does something")
    (project / "Makefile").write_text(renamed, encoding="utf-8")

    found = _violations(project)

    assert any("typecheck" in f for f in found)  # the socle gap
    assert any("type-check" in f for f in found)  # the banned spelling


def test_a_target_with_prerequisites_still_counts(project: Path) -> None:
    """``ci: lint typecheck test`` declares ``ci`` — the socle check must see it."""
    with_prereqs = COMPLETE.replace("ci: ## does something", "ci: lint typecheck test ## does something")
    (project / "Makefile").write_text(with_prereqs, encoding="utf-8")

    assert not any("^ci" in f for f in _violations(project))


def test_a_similar_prefix_does_not_satisfy_a_shorter_target(project: Path) -> None:
    """``test-cov`` must not be mistaken for ``test`` — the colon anchors the name."""
    without_test = COMPLETE.replace("test: ## does something\n\t@true\n", "")
    (project / "Makefile").write_text(without_test, encoding="utf-8")

    found = _violations(project)

    assert len(found) == 1
    assert "^test\\s*:" in found[0]
