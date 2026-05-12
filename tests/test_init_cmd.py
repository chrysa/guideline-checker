"""Tests for the init command."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.cli import main
from guideline_checker.init_cmd import _DEFAULT_INSTRUCTIONS, run_init


class TestRunInit:
    def test_creates_default_instruction_files(self, tmp_path: Path) -> None:
        code = run_init(root=tmp_path)
        assert code == 0
        inst_dir = tmp_path / ".github" / "instructions"
        for filename in _DEFAULT_INSTRUCTIONS:
            assert (inst_dir / filename).exists()

    def test_creates_target_dir_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "custom" / "inst"
        run_init(root=tmp_path, instructions_dir=target)
        assert target.exists()

    def test_skips_existing_files_by_default(self, tmp_path: Path) -> None:
        inst_dir = tmp_path / ".github" / "instructions"
        inst_dir.mkdir(parents=True)
        existing = inst_dir / "python.instructions.md"
        existing.write_text("# existing\n", encoding="utf-8")
        run_init(root=tmp_path)
        # File should NOT be overwritten
        assert existing.read_text(encoding="utf-8") == "# existing\n"

    def test_force_overwrites_existing_files(self, tmp_path: Path) -> None:
        inst_dir = tmp_path / ".github" / "instructions"
        inst_dir.mkdir(parents=True)
        existing = inst_dir / "python.instructions.md"
        existing.write_text("# existing\n", encoding="utf-8")
        run_init(root=tmp_path, force=True)
        content = existing.read_text(encoding="utf-8")
        assert content != "# existing\n"
        assert "applyTo" in content

    def test_custom_instructions_dir(self, tmp_path: Path) -> None:
        custom = tmp_path / "my-rules"
        run_init(root=tmp_path, instructions_dir=custom)
        assert custom.exists()
        assert len(list(custom.glob("*.instructions.md"))) == len(_DEFAULT_INSTRUCTIONS)

    def test_default_files_have_valid_frontmatter(self, tmp_path: Path) -> None:
        run_init(root=tmp_path)
        inst_dir = tmp_path / ".github" / "instructions"
        for filename in _DEFAULT_INSTRUCTIONS:
            content = (inst_dir / filename).read_text(encoding="utf-8")
            assert "applyTo:" in content
            assert "description:" in content


class TestCliInit:
    def test_cli_init_creates_files(self, tmp_path: Path) -> None:
        code = main(["init", "--root", str(tmp_path)])
        assert code == 0
        inst_dir = tmp_path / ".github" / "instructions"
        assert inst_dir.exists()

    def test_cli_init_force_flag(self, tmp_path: Path) -> None:
        inst_dir = tmp_path / ".github" / "instructions"
        inst_dir.mkdir(parents=True)
        (inst_dir / "python.instructions.md").write_text("# old\n", encoding="utf-8")
        code = main(["init", "--root", str(tmp_path), "--force"])
        assert code == 0
        content = (inst_dir / "python.instructions.md").read_text(encoding="utf-8")
        assert content != "# old\n"

    def test_cli_init_custom_instructions_dir(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom-inst"
        code = main(["init", "--root", str(tmp_path), "--instructions", str(custom)])
        assert code == 0
        assert custom.exists()

    def test_cli_init_skip_message_printed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Run twice: second run should skip existing files
        main(["init", "--root", str(tmp_path)])
        main(["init", "--root", str(tmp_path)])
        captured = capsys.readouterr()
        assert "Skipped" in captured.out or "skipped" in captured.out
