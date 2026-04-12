"""Tests for the CLI entry point."""

from __future__ import annotations

from pathlib import Path

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


def test_main_no_command(capsys) -> None:
    code = main([])
    assert code == 0
    captured = capsys.readouterr()
    assert "guideline-checker" in captured.out.lower() or captured.out == ""


def test_main_check_missing_instructions(tmp_path: Path) -> None:
    code = main(["check", "--root", str(tmp_path)])
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
