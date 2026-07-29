"""Tests for guideline_checker.linters module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pytest_mock import MockerFixture

from guideline_checker.linters import (
    LinterResult,
    LinterViolation,
    detect_linters,
    run_linters,
)

# ── detect_linters ────────────────────────────────────────────────────────────


def test_detect_linters_python_only(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    result = detect_linters(tmp_path)
    assert "ruff" in result
    assert "mypy" in result
    assert "eslint" not in result


def test_detect_linters_ts_only(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    result = detect_linters(tmp_path)
    assert "eslint" in result
    assert "ruff" not in result
    assert "mypy" not in result


def test_detect_linters_tsx_file(tmp_path: Path) -> None:
    (tmp_path / "App.tsx").write_text("export default function App() { return null; }\n")
    result = detect_linters(tmp_path)
    assert "eslint" in result


def test_detect_linters_js_file(tmp_path: Path) -> None:
    (tmp_path / "index.js").write_text("const x = 1;\n")
    result = detect_linters(tmp_path)
    assert "eslint" in result


def test_detect_linters_mixed(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    result = detect_linters(tmp_path)
    assert "ruff" in result
    assert "mypy" in result
    assert "eslint" in result


def test_detect_linters_empty_dir(tmp_path: Path) -> None:
    result = detect_linters(tmp_path)
    assert result == []


# ── run_linters ───────────────────────────────────────────────────────────────


def test_run_linters_unknown_linter(tmp_path: Path) -> None:
    results = run_linters(tmp_path, linters=["unknown_tool"])
    assert len(results) == 1
    assert results[0].available is False
    assert "Unknown linter" in (results[0].error or "")


def test_run_linters_auto_detect_empty(tmp_path: Path) -> None:
    results = run_linters(tmp_path, linters=None)
    assert results == []


def test_run_linters_explicit_empty_list(tmp_path: Path) -> None:
    results = run_linters(tmp_path, linters=[])
    assert results == []


def test_linter_result_dataclass() -> None:
    lr = LinterResult(linter="ruff", available=True)
    assert lr.violations == []
    assert lr.error is None


def test_linter_violation_dataclass(tmp_path: Path) -> None:
    v = LinterViolation(
        file=tmp_path / "main.py",
        line=1,
        col=0,
        code="F401",
        message="unused import",
        severity="error",
        linter="ruff",
    )
    assert v.code == "F401"
    assert v.linter == "ruff"


# ── _run_ruff ─────────────────────────────────────────────────────────────────


def test_run_ruff_not_in_path(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_which.return_value = None
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].available is False
    assert "not found" in (results[0].error or "")


def test_run_ruff_no_python_files(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_which.return_value = "/usr/bin/ruff"
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_ruff_success_no_violations(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.return_value = mocker.MagicMock(stdout="[]", returncode=0)
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_ruff_with_violations(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    py_file = tmp_path / "main.py"
    py_file.write_text("import os\n")
    mock_which.return_value = "/usr/bin/ruff"
    violation_json = json.dumps(
        [
            {
                "filename": str(py_file),
                "location": {"row": 1, "column": 1},
                "code": "F401",
                "message": "'os' imported but unused",
                "fix": None,
            }
        ]
    )
    mock_run.return_value = mocker.MagicMock(stdout=violation_json, returncode=1)
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].available is True
    assert len(results[0].violations) == 1
    v = results[0].violations[0]
    assert v.code == "F401"
    assert v.severity == "error"


def test_run_ruff_with_fix_is_warning(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    py_file = tmp_path / "main.py"
    py_file.write_text("import os\n")
    mock_which.return_value = "/usr/bin/ruff"
    violation_json = json.dumps(
        [
            {
                "filename": str(py_file),
                "location": {"row": 1, "column": 1},
                "code": "F401",
                "message": "'os' imported but unused",
                "fix": {"message": "Remove unused import", "edits": []},
            }
        ]
    )
    mock_run.return_value = mocker.MagicMock(stdout=violation_json, returncode=1)
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].violations[0].severity == "warning"


def test_run_ruff_empty_output(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].violations == []


def test_run_ruff_json_parse_error(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.return_value = mocker.MagicMock(stdout="not valid json", returncode=1)
    results = run_linters(tmp_path, linters=["ruff"])
    assert results[0].available is True
    assert "JSON parse error" in (results[0].error or "")


def test_run_ruff_timeout(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="ruff", timeout=120)
    results = run_linters(tmp_path, linters=["ruff"])
    assert "timed out" in (results[0].error or "")


def test_run_ruff_oserror(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/ruff"
    mock_run.side_effect = OSError("permission denied")
    results = run_linters(tmp_path, linters=["ruff"])
    assert "permission denied" in (results[0].error or "")


# ── _run_mypy ─────────────────────────────────────────────────────────────────


def test_run_mypy_not_in_path(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_which.side_effect = lambda cmd: None
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].available is False
    assert "not found" in (results[0].error or "")


def test_run_mypy_no_python_files(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_which.return_value = "/usr/bin/mypy"
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_mypy_success_with_error(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x: int = 'oops'\n")
    mock_which.return_value = "/usr/bin/mypy"
    mypy_line = json.dumps(
        {
            "file": "main.py",
            "line": 1,
            "column": 5,
            "severity": "error",
            "message": "Incompatible types in assignment",
            "code": "assignment",
        }
    )
    mock_run.return_value = mocker.MagicMock(stdout=mypy_line + "\n", stderr="")
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].available is True
    assert len(results[0].violations) == 1
    assert results[0].violations[0].severity == "error"
    assert results[0].violations[0].linter == "mypy"


def test_run_mypy_warning_severity(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/mypy"
    mypy_line = json.dumps(
        {
            "file": "main.py",
            "line": 1,
            "column": 0,
            "severity": "warning",
            "message": "a warning",
            "code": "misc",
        }
    )
    mock_run.return_value = mocker.MagicMock(stdout=mypy_line + "\n", stderr="")
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].violations[0].severity == "warning"


def test_run_mypy_skips_notes(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/mypy"
    note_line = json.dumps(
        {"severity": "note", "message": "a note", "file": "main.py", "line": 1, "column": 0, "code": ""}
    )
    mock_run.return_value = mocker.MagicMock(stdout=note_line + "\n", stderr="")
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].violations == []


def test_run_mypy_skips_non_json_lines(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/mypy"
    # Mix of valid JSON and plain text
    mypy_output = "Success: no issues found\n"
    mock_run.return_value = mocker.MagicMock(stdout=mypy_output, stderr="")
    results = run_linters(tmp_path, linters=["mypy"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_mypy_timeout(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/mypy"
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="mypy", timeout=180)
    results = run_linters(tmp_path, linters=["mypy"])
    assert "timed out" in (results[0].error or "")


def test_run_mypy_oserror(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "main.py").write_text("x = 1\n")
    mock_which.return_value = "/usr/bin/mypy"
    mock_run.side_effect = OSError("not found")
    results = run_linters(tmp_path, linters=["mypy"])
    assert "not found" in (results[0].error or "")


# ── _run_eslint ───────────────────────────────────────────────────────────────


def test_run_eslint_no_ts_js_files(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_which.return_value = "/usr/bin/eslint"
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_eslint_not_found_no_local(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: None
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is False


def test_run_eslint_success_warning(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    ts_file = tmp_path / "app.ts"
    ts_file.write_text("const x: any = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    eslint_output = json.dumps(
        [
            {
                "filePath": str(ts_file),
                "messages": [
                    {
                        "line": 1,
                        "column": 10,
                        "ruleId": "no-explicit-any",
                        "message": "Unexpected any",
                        "severity": 1,
                    }
                ],
            }
        ]
    )
    mock_run.return_value = mocker.MagicMock(stdout=eslint_output, returncode=1)
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is True
    assert len(results[0].violations) == 1
    assert results[0].violations[0].severity == "warning"


def test_run_eslint_severity_error(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    ts_file = tmp_path / "app.ts"
    ts_file.write_text("const x: any = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    eslint_output = json.dumps(
        [
            {
                "filePath": str(ts_file),
                "messages": [
                    {
                        "line": 1,
                        "column": 10,
                        "ruleId": "no-explicit-any",
                        "message": "Unexpected any",
                        "severity": 2,
                    }
                ],
            }
        ]
    )
    mock_run.return_value = mocker.MagicMock(stdout=eslint_output, returncode=1)
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].violations[0].severity == "error"


def test_run_eslint_empty_output(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    mock_run.return_value = mocker.MagicMock(stdout="", returncode=0)
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].violations == []


def test_run_eslint_json_parse_error(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    mock_run.return_value = mocker.MagicMock(stdout="not json", returncode=1)
    results = run_linters(tmp_path, linters=["eslint"])
    assert "JSON parse error" in (results[0].error or "")


def test_run_eslint_timeout(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="eslint", timeout=120)
    results = run_linters(tmp_path, linters=["eslint"])
    assert "timed out" in (results[0].error or "")


def test_run_eslint_oserror(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if cmd == "eslint" else None
    mock_run.side_effect = OSError("no permission")
    results = run_linters(tmp_path, linters=["eslint"])
    assert "no permission" in (results[0].error or "")


# ── _run_biome (via _run_eslint) ──────────────────────────────────────────────


def test_run_biome_success(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    biome_output = json.dumps(
        {
            "diagnostics": [
                {
                    "category": "lint/suspicious/noExplicitAny",
                    "description": "Using any disables type safety",
                    "severity": "warning",
                    "location": {
                        "path": {"file": "app.ts"},
                        "span": {"start": {"line": 0, "character": 6}},
                    },
                }
            ]
        }
    )
    mock_run.return_value = mocker.MagicMock(stdout=biome_output, stderr="")
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is True
    assert len(results[0].violations) == 1
    assert results[0].violations[0].linter == "biome"


def test_run_biome_no_span(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    biome_output = json.dumps(
        {
            "diagnostics": [
                {
                    "category": "lint/suspicious/noExplicitAny",
                    "description": "Using any disables type safety",
                    "severity": "error",
                    "location": {"path": {"file": "app.ts"}},
                }
            ]
        }
    )
    mock_run.return_value = mocker.MagicMock(stdout=biome_output, stderr="")
    results = run_linters(tmp_path, linters=["eslint"])
    assert len(results[0].violations) == 1
    assert results[0].violations[0].line == 0
    assert results[0].violations[0].severity == "error"


def test_run_biome_empty_output(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    mock_run.return_value = mocker.MagicMock(stdout="", stderr="")
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].violations == []


def test_run_biome_json_error(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    mock_run.return_value = mocker.MagicMock(stdout="not json", stderr="")
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is True
    assert results[0].violations == []


def test_run_biome_timeout(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="biome", timeout=60)
    results = run_linters(tmp_path, linters=["eslint"])
    assert "timed out" in (results[0].error or "")


def test_run_biome_oserror(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    mock_which.side_effect = lambda cmd: "/usr/bin/biome" if cmd == "biome" else None
    mock_run.side_effect = OSError("biome crashed")
    results = run_linters(tmp_path, linters=["eslint"])
    assert "biome crashed" in (results[0].error or "")


# ── local eslint fallback ─────────────────────────────────────────────────────


def test_run_eslint_uses_local_binary(tmp_path: Path, mocker: MockerFixture) -> None:
    mock_which = mocker.patch("guideline_checker.linters.shutil.which")
    mock_run = mocker.patch("guideline_checker.linters.subprocess.run")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    # Create a fake local eslint binary
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    local_eslint = local_bin / "eslint"
    local_eslint.write_text("#!/bin/sh\necho '[]\n'")
    local_eslint.chmod(0o755)
    # Neither biome nor global eslint found
    mock_which.side_effect = lambda cmd: None
    mock_run.return_value = mocker.MagicMock(stdout="[]", returncode=0)
    results = run_linters(tmp_path, linters=["eslint"])
    assert results[0].available is True
