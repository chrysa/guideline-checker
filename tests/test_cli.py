"""Tests for the CLI entry point."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from guideline_checker.cli import build_parser, main


def _make_project(tmp_path: Path, *, violation: bool = True) -> Path:
    """Create a minimal project with an instruction file."""
    root = tmp_path / "project"
    root.mkdir()
    inst_dir = root / ".github" / "instructions"
    inst_dir.mkdir(parents=True)
    (inst_dir / "rules.instructions.md").write_text(
        '---\napplyTo: "**/*.py"\ndescription: "Test rules"\n---\n- No print() calls\n',
        encoding="utf-8",
    )
    src = root / "src"
    src.mkdir()
    code = 'print("bad")\n' if violation else "x = 1\n"
    (src / "app.py").write_text(code, encoding="utf-8")
    return root


def test_build_parser_has_check_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["check"])
    assert args.command == "check"


def test_build_parser_default_values() -> None:
    parser = build_parser()
    args = parser.parse_args(["check"])
    assert args.root == Path(".")
    assert args.instructions is None
    assert args.output == Path("guideline-report.html")
    assert args.fail_on == "error"
    assert args.json is None


def test_main_no_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([])
    assert code == 0
    captured = capsys.readouterr()
    assert "guideline-checker" in captured.out.lower() or captured.out == ""


def test_main_check_missing_instructions(tmp_path: Path) -> None:
    code = main(["check", "--root", str(tmp_path), "--no-multi-source"])
    assert code == 1


def test_main_check_no_violations(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=False)
    code = main(["check", "--root", str(root)])
    assert code == 0


def test_main_check_with_violations_fail_on_warning(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=True)
    code = main(["check", "--root", str(root), "--fail-on", "warning"])
    assert code == 1


def test_main_check_with_violations_fail_on_never(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=True)
    code = main(["check", "--root", str(root), "--fail-on", "never"])
    assert code == 0


def test_main_check_html_report_created(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=True)
    report = tmp_path / "report.html"
    main(["check", "--root", str(root), "--output", str(report)])
    assert report.exists()
    content = report.read_text(encoding="utf-8")
    assert "Guideline Compliance Report" in content


def test_main_check_json_report_created(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=True)
    json_report = tmp_path / "report.json"
    main(["check", "--root", str(root), "--json", str(json_report)])
    assert json_report.exists()
    import json

    data = json.loads(json_report.read_text(encoding="utf-8"))
    assert "summary" in data
    assert "rules" in data


def test_main_check_custom_instructions_dir(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    custom_inst = tmp_path / "custom_instructions"
    custom_inst.mkdir()
    (custom_inst / "rules.instructions.md").write_text(
        '---\napplyTo: "**/*.py"\ndescription: "Custom"\n---\n- No print() calls\n',
        encoding="utf-8",
    )
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    code = main(["check", "--root", str(root), "--instructions", str(custom_inst)])
    assert code == 0


def test_main_check_sarif_report_created(tmp_path: Path) -> None:
    import json

    root = _make_project(tmp_path, violation=True)
    sarif_report = tmp_path / "report.sarif"
    main(["check", "--root", str(root), "--sarif", str(sarif_report), "--fail-on", "never"])
    assert sarif_report.exists()
    data = json.loads(sarif_report.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"


def test_main_check_markdown_report_created(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=True)
    md_report = tmp_path / "report.md"
    main(["check", "--root", str(root), "--markdown", str(md_report), "--fail-on", "never"])
    assert md_report.exists()
    content = md_report.read_text(encoding="utf-8")
    assert "# Guideline Compliance Report" in content


def test_main_check_fail_on_error_no_violations(tmp_path: Path) -> None:
    root = _make_project(tmp_path, violation=False)
    code = main(["check", "--root", str(root), "--fail-on", "error"])
    assert code == 0


def test_main_check_fail_on_warning_with_warning_only(tmp_path: Path) -> None:
    """fail-on=warning should exit 1 when there are warnings but no errors."""
    root = _make_project(tmp_path, violation=True)  # print() → severity warning
    code = main(["check", "--root", str(root), "--fail-on", "warning"])
    assert code == 1


# ─── web subcommand ────────────────────────────────────────────────────────────


def test_build_parser_has_web_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["web"])
    assert args.command == "web"
    assert args.root == Path(".")
    assert args.host == "127.0.0.1"
    assert args.port == 8080
    assert args.reload is False


def test_main_web_launches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_ROOT", "placeholder")  # register for teardown restoration
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    code = main(["web", "--root", str(tmp_path), "--port", "9999"])

    assert code == 0
    assert os.environ["SCAN_ROOT"] == str(tmp_path.resolve())
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999


def test_main_web_reload_uses_import_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_ROOT", "placeholder")
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    code = main(["web", "--root", str(tmp_path), "--reload"])

    assert code == 0
    assert captured["app"] == "guideline_checker.web.app:app"
    assert captured["reload"] is True


def test_main_web_missing_uvicorn(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_import = importlib.import_module

    def raiser(name: str, *a: object, **k: object) -> object:
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", raiser)

    code = main(["web"])

    assert code == 1
    assert "guideline-checker[web]" in capsys.readouterr().err


def test_main_web_warns_on_open_public_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCAN_ROOT", "placeholder")
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)

    code = main(["web", "--root", str(tmp_path), "--host", "0.0.0.0"])

    assert code == 0
    assert "WARNING" in capsys.readouterr().err


class TestSynthesizeOrigin:
    def test_origin_source_writes_report_and_returns_zero(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from guideline_checker.gh_client import GhClient as RealClient
        from guideline_checker.gh_client import GhResult

        def _origin_runner(args):  # type: ignore[no-untyped-def]
            joined = " ".join(args)
            if joined.endswith("--jq .name"):
                return GhResult(True, "alpha\n", "", 0)
            if joined.endswith("--jq .default_branch"):
                return GhResult(True, "main\n", "", 0)
            return GhResult(False, "", "404", 1)  # all artifacts absent → drift

        manifest = tmp_path / "repos.yml"
        manifest.write_text("repos:\n  - name: alpha\n    status: dev\n", encoding="utf-8")
        shared = tmp_path / "shared-standards"
        (shared / "standards").mkdir(parents=True)
        (shared / "templates").mkdir(parents=True)
        (shared / "standards" / "STANDARDS.chrysa.md").write_text("CANON\n", encoding="utf-8")
        (shared / "templates" / "LICENSE.mit").write_text("MIT\n", encoding="utf-8")
        out = tmp_path / "synthesis.html"

        from guideline_checker.cli import main

        with patch("guideline_checker.cli.GhClient") as gh_cls:
            gh_cls.return_value = RealClient(runner=_origin_runner)
            code = main(
                [
                    "synthesize",
                    "--source",
                    "origin",
                    "--manifest",
                    str(manifest),
                    "--shared-standards",
                    str(shared),
                    "--workspace",
                    str(tmp_path),
                    "--output",
                    str(out),
                ]
            )
        assert code == 0
        assert out.exists()
