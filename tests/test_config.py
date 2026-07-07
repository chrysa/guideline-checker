"""Tests for the project config loader ([tool.guideline-checker], L2.3)."""

from __future__ import annotations

from pathlib import Path

from guideline_checker.config import load_config


def _write_pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(body, encoding="utf-8")


class TestLoadConfig:
    def test_missing_files_yield_empty(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.values == {}
        assert cfg.warnings == []

    def test_reads_tool_table_from_pyproject(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[tool.guideline-checker]\nfail_on = "warning"\nmax_file_size = 300000\n'
            'exclude = ["tests", "scripts/**"]\nlinters = ["ruff", "mypy"]\n'
            'baseline = ".guideline-baseline.json"\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.warnings == []
        assert cfg.values == {
            "fail_on": "warning",
            "max_file_size": 300000,
            "exclude": ["tests", "scripts/**"],
            "linters": ["ruff", "mypy"],
            "baseline": ".guideline-baseline.json",
        }

    def test_pyproject_without_table_falls_back_to_toml(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "demo"\n')
        (tmp_path / ".guideline-checker.toml").write_text('fail_on = "never"\n', encoding="utf-8")
        cfg = load_config(tmp_path)
        assert cfg.values == {"fail_on": "never"}

    def test_dedicated_toml_supports_tool_table(self, tmp_path: Path) -> None:
        (tmp_path / ".guideline-checker.toml").write_text(
            '[tool.guideline-checker]\nfail_on = "warning"\n', encoding="utf-8"
        )
        cfg = load_config(tmp_path)
        assert cfg.values == {"fail_on": "warning"}

    def test_pyproject_wins_over_toml(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[tool.guideline-checker]\nfail_on = "error"\n')
        (tmp_path / ".guideline-checker.toml").write_text('fail_on = "never"\n', encoding="utf-8")
        cfg = load_config(tmp_path)
        assert cfg.values["fail_on"] == "error"

    def test_unknown_key_warns_and_is_dropped(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[tool.guideline-checker]\nnope = 1\nfail_on = "warning"\n')
        cfg = load_config(tmp_path)
        assert cfg.values == {"fail_on": "warning"}
        assert any("nope" in w for w in cfg.warnings)

    def test_invalid_fail_on_warns_and_is_dropped(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[tool.guideline-checker]\nfail_on = "maybe"\n')
        cfg = load_config(tmp_path)
        assert "fail_on" not in cfg.values
        assert any("fail_on" in w for w in cfg.warnings)

    def test_invalid_types_warn_and_are_dropped(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[tool.guideline-checker]\nmax_file_size = "big"\nexclude = "tests"\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.values == {}
        assert len(cfg.warnings) == 2

    def test_bool_is_not_accepted_as_int(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, "[tool.guideline-checker]\nmax_file_size = true\n")
        cfg = load_config(tmp_path)
        assert "max_file_size" not in cfg.values
        assert cfg.warnings
