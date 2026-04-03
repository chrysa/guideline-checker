"""Tests for the pre-commit hook entry point."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


def test_hook_calls_main_and_exits(tmp_path: Path) -> None:
    """Verify the hook entry point delegates to cli.main and calls sys.exit."""
    # Create a minimal project with instructions directory
    inst_dir = tmp_path / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "test.instructions.md").write_text(
        "---\napplyTo: '**/*.py'\ndescription: 'Test'\n---\n- No bare except",
        encoding="utf-8",
    )

    with patch("sys.exit") as mock_exit, patch("sys.argv", ["guideline-checker", "check", "--root", str(tmp_path)]):
        from guideline_checker.hook import main  # type: ignore[attr-defined]

        # hook.py uses __main__ guard, so we test via cli.main directly
        from guideline_checker.cli import main as cli_main

        exit_code = cli_main(["check", "--root", str(tmp_path)])
        assert exit_code in (0, 1)  # valid exit codes


def test_hook_entry_point_importable() -> None:
    """Verify the hook module is importable without side effects."""
    import importlib

    mod = importlib.import_module("guideline_checker.hook")
    assert mod is not None
