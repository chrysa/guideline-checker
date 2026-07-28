"""Repository lifecycle checks (decision D-025).

The contract is not "does it find things" but "does it refuse to endanger work":
a dirty repository is never proposed for cleanup, and a tracked artefact is
untracked rather than deleted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from guideline_checker.lifecycle import (
    CHECK_IDS,
    audit,
    check_liveness,
    check_scratch_files,
    check_tracked_artefacts,
    check_worktrees,
)


def _git(path: Path, *args: str) -> None:
    """Run git isolated from the machine's global configuration.

    Without this, every test commit fires the user's global hooks
    (``core.hooksPath``): the suite becomes slow enough to time out and its
    result depends on the workstation.
    """
    subprocess.run(
        ["git", "-c", "core.hooksPath=", "-C", str(path), *args],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "living"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("# Active project\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "initial")
    return repo


def _rules(violations: list) -> set[str]:
    return {v.rule for v in violations}


# --- liveness ---------------------------------------------------------------


def test_a_fresh_repository_raises_nothing(repository: Path) -> None:
    assert check_liveness(repository) == []


def test_a_missing_path_is_reported(tmp_path: Path) -> None:
    assert _rules(check_liveness(tmp_path / "gone")) == {"repo-missing"}


def test_a_deprecation_notice_in_the_readme_is_detected(repository: Path) -> None:
    (repository / "README.md").write_text("# Old\n\nThis project is deprecated.\n", encoding="utf-8")
    assert "repo-deprecated" in _rules(check_liveness(repository))


def test_an_archive_marker_in_the_name_is_detected(tmp_path: Path) -> None:
    legacy = tmp_path / "project-legacy"
    legacy.mkdir()
    assert "repo-deprecated" in _rules(check_liveness(legacy))


def test_a_recent_commit_is_not_called_inactive(repository: Path) -> None:
    assert "repo-inactive" not in _rules(check_liveness(repository, stale_after_days=365))


def test_the_staleness_threshold_is_configurable(repository: Path) -> None:
    assert "repo-inactive" in _rules(check_liveness(repository, stale_after_days=0))


# --- tracked artefacts ------------------------------------------------------


def test_a_committed_cache_is_reported_as_an_error(repository: Path) -> None:
    cache = repository / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"\x00" * 32)
    _git(repository, "add", "-f", "src/__pycache__")
    _git(repository, "commit", "-qm", "oops")

    violations = check_tracked_artefacts(repository)
    assert [str(v.file) for v in violations] == ["src/__pycache__"]
    assert violations[0].severity == "error"
    assert "untrack" in violations[0].line_content


def test_a_clean_repository_tracks_no_artefact(repository: Path) -> None:
    assert check_tracked_artefacts(repository) == []


def test_the_offending_directory_is_reported_once_not_every_file(repository: Path) -> None:
    """Proposing four thousand removals would be unusable."""
    cache = repository / "build"
    cache.mkdir()
    for index in range(5):
        (cache / f"out{index}.bin").write_bytes(b"\x00")
    _git(repository, "add", "-f", "build")
    _git(repository, "commit", "-qm", "build")
    assert len(check_tracked_artefacts(repository)) == 1


# --- worktrees --------------------------------------------------------------


def test_an_agent_worktree_is_reported(repository: Path, tmp_path: Path) -> None:
    worktree = tmp_path / ".claude" / "worktrees" / "side"
    _git(repository, "worktree", "add", "-q", str(worktree), "-b", "side")
    assert "abandoned-worktree" in _rules(check_worktrees(repository))


def test_the_main_worktree_is_never_reported(repository: Path) -> None:
    assert check_worktrees(repository) == []


# --- scratch files ----------------------------------------------------------


def test_a_scratch_file_at_the_root_is_reported(repository: Path) -> None:
    (repository / "scratch_run.json").write_text("{}", encoding="utf-8")
    assert [str(v.file) for v in check_scratch_files(repository)] == ["scratch_run.json"]


def test_ordinary_files_are_left_alone(repository: Path) -> None:
    (repository / "notes.md").write_text("keep", encoding="utf-8")
    assert check_scratch_files(repository) == []


# --- the safety contract ----------------------------------------------------


def test_uncommitted_work_suspends_every_cleanup_check(repository: Path) -> None:
    """A dirty repository may hold unsaved work: nothing is proposed on it."""
    (repository / "scratch_run.json").write_text("{}", encoding="utf-8")
    (repository / "wip.txt").write_text("work in progress", encoding="utf-8")

    report = audit(repository)
    assert report.dirty is True
    assert "scratch-file" not in _rules(report.violations)
    assert "repo-inactive" in _rules(report.violations)


def test_a_clean_repository_gets_the_full_audit(repository: Path) -> None:
    (repository / "scratch_run.json").write_text("{}", encoding="utf-8")
    _git(repository, "add", "scratch_run.json")
    _git(repository, "commit", "-qm", "scratch")

    report = audit(repository)
    assert report.dirty is False
    assert "scratch-file" in _rules(report.violations)


def test_a_missing_repository_is_reported_not_crashed(tmp_path: Path) -> None:
    report = audit(tmp_path / "gone")
    assert report.exists is False
    assert "repo-missing" in _rules(report.violations)


def test_every_emitted_rule_is_declared(repository: Path) -> None:
    """An undeclared rule id would be invisible to the reporters."""
    (repository / "scratch_run.json").write_text("{}", encoding="utf-8")
    _git(repository, "add", "scratch_run.json")
    _git(repository, "commit", "-qm", "scratch")
    assert _rules(audit(repository).violations) <= set(CHECK_IDS)
