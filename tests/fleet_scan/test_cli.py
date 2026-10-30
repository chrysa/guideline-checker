"""`guideline-checker fleet-scan ...` wiring: lazy import, run/resume/report through main()."""

from __future__ import annotations

import builtins
import importlib
from functools import partial
from pathlib import Path

import pytest

from guideline_checker import cli as main_cli
from guideline_checker.cli import main
from guideline_checker.fleet.scan import cli as scan_cli
from guideline_checker.fleet.scan.github import GithubClient

from .conftest import FakeGithub, no_sleep


@pytest.fixture
def wired(monkeypatch, fake_github: FakeGithub, tmp_path: Path):
    """Route the CLI's GithubClient to the fake transport and run inside tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        scan_cli, "GithubClient", partial(GithubClient, transport=fake_github.transport, sleep=no_sleep)
    )
    return tmp_path


def _args(config_dir: Path, *extra: str) -> list[str]:
    return ["fleet-scan", *extra[:1], "--config-dir", str(config_dir), *extra[1:]]


def test_missing_scan_extra_gives_a_friendly_error(monkeypatch, capsys) -> None:
    real_import = importlib.import_module

    def fake_import(name: str, *args, **kwargs):
        if name == "guideline_checker.fleet.scan.cli":
            raise ModuleNotFoundError("No module named 'pydantic'", name="pydantic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(main_cli.importlib, "import_module", fake_import)
    assert main(["fleet-scan", "validate-config"]) == 2
    err = capsys.readouterr().err
    assert "pip install 'guideline-checker[scan]'" in err
    assert "pydantic" in err


def test_arguments_module_needs_no_optional_dependency(monkeypatch) -> None:
    blocked = {"pydantic", "httpx"}
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    module = importlib.reload(importlib.import_module("guideline_checker.fleet.scan.arguments"))
    assert callable(module.add_fleet_scan_arguments)


def test_no_subcommand_is_usage_error(capsys) -> None:
    assert main(["fleet-scan"]) == 2
    assert "usage" in capsys.readouterr().err


def test_run_subset_then_report(wired: Path, fixture_config_dir: Path, capsys) -> None:
    assert main(_args(fixture_config_dir, "run", "--subset", "pilot")) == 0
    out = capsys.readouterr().out
    assert "scope: requested 3 | collected 2 | evaluated 2 | excluded 1 | errors 0" in out
    run_dirs = list((wired / "runs").iterdir())
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name
    assert (run_dirs[0] / "report.json").is_file()
    assert (wired / "history.jsonl").is_file()
    (run_dirs[0] / "report.md").unlink()
    assert main(_args(fixture_config_dir, "report", "--run-id", run_id)) == 0
    assert (run_dirs[0] / "report.md").is_file()


def test_run_explicit_repos_with_output_dir(wired: Path, fixture_config_dir: Path, capsys) -> None:
    out_dir = wired / "elsewhere"
    code = main(_args(fixture_config_dir, "run", "--repos", "chrysa/alpha,ghost/nowhere", "--output-dir", str(out_dir)))
    assert code == 0
    assert "errors 1" in capsys.readouterr().out
    assert len(list(out_dir.iterdir())) == 1


def test_resume_refuses_scope_flags_and_unknown_run(wired: Path, fixture_config_dir: Path, capsys) -> None:
    assert main(_args(fixture_config_dir, "run", "--resume", "x", "--subset", "pilot")) == 2
    assert "--resume takes the scope" in capsys.readouterr().err
    assert main(_args(fixture_config_dir, "run", "--resume", "20260101T000000Z-abcdef")) == 2
    assert "resume refused" in capsys.readouterr().err


def test_resume_completed_run_is_a_no_op_collection(wired: Path, fixture_config_dir: Path, fake_github, capsys) -> None:
    assert main(_args(fixture_config_dir, "run", "--subset", "pilot")) == 0
    run_id = next((wired / "runs").iterdir()).name
    fake_github.calls.clear()
    assert main(_args(fixture_config_dir, "run", "--resume", run_id)) == 0
    assert fake_github.calls == []


def test_required_token_missing_is_config_error(wired: Path, fixture_config_dir: Path, monkeypatch, capsys) -> None:
    path = fixture_config_dir / "github.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("required: false", "required: true"), encoding="utf-8")
    monkeypatch.delenv("FLEET_SCAN_TEST_TOKEN", raising=False)
    assert main(_args(fixture_config_dir, "run", "--subset", "pilot")) == 2
    err = capsys.readouterr().err
    assert "FLEET_SCAN_TEST_TOKEN is required" in err


def test_token_value_never_printed(wired: Path, fixture_config_dir: Path, monkeypatch, capsys) -> None:
    planted_token = "ghp_" + "Q" * 36
    monkeypatch.setenv("FLEET_SCAN_TEST_TOKEN", planted_token)
    assert main(_args(fixture_config_dir, "run", "--subset", "pilot")) == 0
    captured = capsys.readouterr()
    assert planted_token not in captured.out + captured.err
    for path in wired.rglob("*"):
        if path.is_file():
            assert planted_token not in path.read_text(encoding="utf-8", errors="replace")


def test_unknown_subset_and_empty_scope(wired: Path, fixture_config_dir: Path, capsys) -> None:
    assert main(_args(fixture_config_dir, "run", "--subset", "nope")) == 2
    assert "unknown subset" in capsys.readouterr().err


def test_interrupt_returns_130(wired: Path, fixture_config_dir: Path, monkeypatch, capsys) -> None:
    def interrupted(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(scan_cli.asyncio, "run", interrupted)
    assert main(_args(fixture_config_dir, "run", "--subset", "pilot")) == 130
    assert "interrupted; resume with" in capsys.readouterr().err
