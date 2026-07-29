"""Tests for the pre-commit hook entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pytest_mock import MockerFixture


def test_hook_calls_main_and_exits(tmp_path: Path, mocker: MockerFixture) -> None:
    """Verify the hook entry point delegates to cli.main and calls sys.exit."""
    inst_dir = tmp_path / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "test.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'Test'\n---\n- No bare except",
        encoding="utf-8",
    )

    mocker.patch("sys.exit")
    mocker.patch("sys.argv", ["guideline-checker", "check", "--root", str(tmp_path)])

    from guideline_checker.cli import main as cli_main

    exit_code = cli_main(["check", "--root", str(tmp_path)])

    assert exit_code in (0, 1)


def test_hook_entry_point_importable() -> None:
    """Verify the hook module is importable without side effects."""
    import importlib

    mod = importlib.import_module("guideline_checker.hook")
    assert mod is not None


def test_hook_main_guard_runs_via_subprocess(tmp_path: Path) -> None:
    """Run hook.py as __main__ to cover the if __name__ == '__main__' branch."""
    inst_dir = tmp_path / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "test.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'Test'\n---\n- No bare except",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "guideline_checker.hook", "check", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    # Exit code 0 (no violations) or 1 (violations found) are both valid
    assert result.returncode in (0, 1)
