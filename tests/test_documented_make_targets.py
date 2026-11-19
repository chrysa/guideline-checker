"""The shipped rule that reads two files at once: docs cite, the Makefile defines.

`CHANGELOG.md` told readers to run `make changelog` for months while no such
target existed. Neither file was wrong on its own, which is why every single-file
mechanism was blind to it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guideline_checker.core.detection import run_checks
from guideline_checker.guidelines import load_yaml_guidelines

REFERENTIAL = Path(__file__).resolve().parents[1] / "guidelines"

RULE = "Every make target cited in the documentation exists in the Makefile"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project carrying the shipped referential.

    Read from the *scanned root*, not the installed package — without the copy
    the fixture silently checks nothing.
    """
    shutil.copytree(REFERENTIAL, tmp_path / "guidelines")
    return tmp_path


def _findings(root: Path) -> list[str]:
    return [
        v.line_content
        for result in run_checks(root, all_sources=True)
        for v in result.violations
        if v.file.suffix == ".md"
    ]


def test_a_documented_target_that_does_not_exist_is_flagged(project: Path) -> None:
    (project / "Makefile").write_text("lint:\n\t@ruff check .\n", encoding="utf-8")
    (project / "README.md").write_text("Run `make deploy` to ship.\n", encoding="utf-8")

    found = _findings(project)

    assert len(found) == 1
    assert "deploy" in found[0]
    assert "Makefile" in found[0]  # says where the definition was expected


def test_a_documented_target_that_exists_is_left_alone(project: Path) -> None:
    (project / "Makefile").write_text("lint:\n\t@ruff check .\n", encoding="utf-8")
    (project / "README.md").write_text("Run `make lint` before pushing.\n", encoding="utf-8")

    assert _findings(project) == []


def test_prose_is_not_mistaken_for_a_command(project: Path) -> None:
    """ "make sure", "make it clear" — why the citation is scoped to backticks.

    An unscoped ``make (\\w+)`` matched "it", "the", "sure" and "wrapper" on this
    repo's own docs. A rule that cries wolf on English is worse than no rule.
    """
    (project / "Makefile").write_text("lint:\n\t@ruff check .\n", encoding="utf-8")
    (project / "README.md").write_text("Make sure to make it clear, and make the change small.\n", encoding="utf-8")

    assert _findings(project) == []


def test_a_target_declared_with_prerequisites_counts_as_defined(project: Path) -> None:
    (project / "Makefile").write_text("ci: lint typecheck test\n\t@true\n", encoding="utf-8")
    (project / "README.md").write_text("Run `make ci`.\n", encoding="utf-8")

    assert _findings(project) == []


def test_the_shipped_rule_keeps_its_detector() -> None:
    """Drift guard: the rule is nothing without its cross-reference."""
    repo_root = Path(__file__).resolve().parents[1]
    instructions = load_yaml_guidelines(repo_root)
    detectors = {rule: d for i in instructions for rule, d in i.rule_detectors.items()}

    reference = detectors[RULE].cross_reference

    assert reference is not None
    assert reference.define_in == ("Makefile",)
