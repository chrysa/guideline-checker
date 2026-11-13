"""Tests for the --diff mode (git-diff restricted scanning)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from guideline_checker.cli import _get_diff_files, main
from guideline_checker.core.detection import run_checks


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with one instruction file and two Python files."""
    root = tmp_path / "proj"
    root.mkdir()
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "rules.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'Test'\n---\n- No print() calls\n",
        encoding="utf-8",
    )
    (root / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (root / "dirty.py").write_text('print("bad")\n', encoding="utf-8")
    return root, inst_dir


class TestGetDiffFiles:
    @pytest.fixture(autouse=True)
    def _git_on_path(self, mocker: MockerFixture) -> Iterator[None]:
        """Pretend git is installed so tests do not depend on the host/container PATH.

        ``_get_diff_files`` guards on ``shutil.which("git")`` (subprocess hardening);
        without this the guard short-circuits to None in git-less images.
        """
        mocker.patch("shutil.which", return_value="/usr/bin/git")
        yield

    def test_returns_paths_from_git_output(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.MagicMock(returncode=0, stdout="src/app.py\nsrc/util.py\n")
        result = _get_diff_files(tmp_path)
        assert result is not None
        assert len(result) == 2
        assert result[0] == tmp_path / "src" / "app.py"

    def test_returns_none_when_git_not_found(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)
        result = _get_diff_files(tmp_path)
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mocker.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10))
        result = _get_diff_files(tmp_path)
        assert result is None

    def test_falls_back_to_cached_on_nonzero_head(self, tmp_path: Path, mocker: MockerFixture) -> None:
        responses = [
            mocker.MagicMock(returncode=128, stdout=""),  # git diff --name-only HEAD fails (no HEAD)
            mocker.MagicMock(returncode=0, stdout="new_file.py\n"),  # git diff --name-only --cached
        ]
        mocker.patch("subprocess.run", side_effect=responses)
        result = _get_diff_files(tmp_path)
        assert result is not None
        assert len(result) == 1

    def test_returns_none_when_both_git_calls_fail(self, tmp_path: Path, mocker: MockerFixture) -> None:
        responses = [
            mocker.MagicMock(returncode=128, stdout=""),
            mocker.MagicMock(returncode=128, stdout=""),
        ]
        mocker.patch("subprocess.run", side_effect=responses)
        result = _get_diff_files(tmp_path)
        assert result is None

    def test_returns_empty_list_when_no_diff(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value = mocker.MagicMock(returncode=0, stdout="")
        result = _get_diff_files(tmp_path)
        assert result == []


class TestRunChecksWithDiffFiles:
    def test_diff_files_restricts_checked_files(self, tmp_path: Path) -> None:
        root, inst_dir = _make_project(tmp_path)
        # Only pass dirty.py as a diff file
        diff_files = [root / "dirty.py"]
        results = run_checks(root=root, instructions_dir=inst_dir, diff_files=diff_files)
        all_violations = [v for r in results for v in r.violations]
        assert any("print(" in v.line_content for v in all_violations)
        # clean.py was not in diff_files — no violation from it
        assert not any("clean.py" in str(v.file) for v in all_violations)

    def test_diff_files_none_checks_all(self, tmp_path: Path) -> None:
        root, inst_dir = _make_project(tmp_path)
        results = run_checks(root=root, instructions_dir=inst_dir, diff_files=None)
        all_violations = [v for r in results for v in r.violations]
        assert any("print(" in v.line_content for v in all_violations)


class TestCliDiffFlag:
    def test_diff_flag_no_modified_files_exits_zero(self, tmp_path: Path, mocker: MockerFixture) -> None:
        root, _ = _make_project(tmp_path)
        mocker.patch("guideline_checker.cli._get_diff_files", return_value=[])
        code = main(["check", "--root", str(root), "--diff"])
        assert code == 0

    def test_diff_flag_with_violations_exits_nonzero(self, tmp_path: Path, mocker: MockerFixture) -> None:
        root, _ = _make_project(tmp_path)
        mocker.patch("guideline_checker.cli._get_diff_files", return_value=[root / "dirty.py"])
        code = main(["check", "--root", str(root), "--diff", "--fail-on", "warning"])
        assert code == 1

    def test_diff_flag_git_unavailable_falls_back_to_all(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mocker: MockerFixture
    ) -> None:
        root, _ = _make_project(tmp_path)
        mocker.patch("guideline_checker.cli._get_diff_files", return_value=None)
        code = main(["check", "--root", str(root), "--diff", "--fail-on", "never"])
        captured = capsys.readouterr()
        assert "not available" in captured.err or "git not available" in captured.err
        assert code == 0
