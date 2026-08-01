"""External linter integration: ruff, mypy, eslint/biome.

Each linter is run as a subprocess and its output is parsed into a unified
``LinterViolation`` / ``LinterResult`` structure.  Linters that are not
installed or that fail to parse are reported with ``available=False`` and
an ``error`` message — they never crash the main check flow.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ─── Data structures ──────────────────────────────────────────────────────────


@dataclass
class LinterViolation:
    file: Path
    line: int
    col: int
    code: str
    message: str
    severity: str  # "error" | "warning"
    linter: str


@dataclass
class LinterResult:
    linter: str
    available: bool
    violations: list[LinterViolation] = field(default_factory=list)
    error: str | None = None


# ─── Individual linter runners ────────────────────────────────────────────────


def _parse_ruff_output(raw: str) -> LinterResult:
    """Turn ``ruff check --output-format json`` stdout into a :class:`LinterResult`.

    Split out from :func:`_run_ruff` so the parsing is testable on a fixture string,
    with no ruff on PATH and no subprocess.
    """
    if not raw:
        return LinterResult(linter="ruff", available=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LinterResult(linter="ruff", available=True, error=f"JSON parse error: {exc}")

    violations = [
        LinterViolation(
            file=Path(item.get("filename", "")),
            line=item.get("location", {}).get("row", 0),
            col=item.get("location", {}).get("column", 0),
            code=item.get("code", ""),
            message=item.get("message", ""),
            # A finding ruff can fix itself is a warning; one needing a human is an error.
            severity="warning" if item.get("fix") else "error",
            linter="ruff",
        )
        for item in data
    ]
    return LinterResult(linter="ruff", available=True, violations=violations)


def _run_ruff(root: Path) -> LinterResult:
    """Run ``ruff check`` on *root* and return a :class:`LinterResult`."""
    ruff_path = shutil.which("ruff")
    if ruff_path is None:
        return LinterResult(linter="ruff", available=False, error="ruff not found in PATH")

    # Check if there are Python files
    has_python = any(root.rglob("*.py"))
    if not has_python:
        return LinterResult(linter="ruff", available=True)

    try:
        result = subprocess.run(
            [
                ruff_path,
                "check",
                "--output-format",
                "json",
                "--no-cache",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
        )
        return _parse_ruff_output(result.stdout.strip())

    except subprocess.TimeoutExpired:
        return LinterResult(linter="ruff", available=True, error="ruff timed out (>120s)")
    except OSError as exc:
        return LinterResult(linter="ruff", available=True, error=str(exc))


def _run_mypy(root: Path) -> LinterResult:
    """Run ``mypy`` on *root* (Python files) and return a :class:`LinterResult`."""
    mypy_path = shutil.which("mypy")
    if mypy_path is None:
        return LinterResult(linter="mypy", available=False, error="mypy not found in PATH")

    has_python = any(root.rglob("*.py"))
    if not has_python:
        return LinterResult(linter="mypy", available=True)

    try:
        result = subprocess.run(
            [
                mypy_path,
                "--output=json",
                "--ignore-missing-imports",
                "--no-error-summary",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=root,
        )
        lines = (result.stdout + result.stderr).splitlines()
        violations: list[LinterViolation] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            severity_raw = item.get("severity", "error")
            if severity_raw == "note":
                continue
            file_path = Path(item.get("file", ""))
            violations.append(
                LinterViolation(
                    file=file_path,
                    line=item.get("line", 0),
                    col=item.get("column", 0),
                    code=item.get("code", ""),
                    message=item.get("message", ""),
                    severity="error" if severity_raw == "error" else "warning",
                    linter="mypy",
                )
            )
        return LinterResult(linter="mypy", available=True, violations=violations)

    except subprocess.TimeoutExpired:
        return LinterResult(linter="mypy", available=True, error="mypy timed out (>180s)")
    except OSError as exc:
        return LinterResult(linter="mypy", available=True, error=str(exc))


def _parse_eslint_output(raw: str) -> LinterResult:
    """Turn ``eslint --format json`` stdout into a :class:`LinterResult`.

    Split out from :func:`_run_eslint` so the parsing is testable on a fixture
    string, with no node toolchain and no subprocess.
    """
    if not raw:
        return LinterResult(linter="eslint", available=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LinterResult(linter="eslint", available=True, error=f"JSON parse error: {exc}")

    violations = [
        LinterViolation(
            file=Path(file_item.get("filePath", "")),
            line=msg.get("line", 0),
            col=msg.get("column", 0),
            code=msg.get("ruleId") or "",
            message=msg.get("message", ""),
            # eslint severity 2 is an error, 1 a warning.
            severity="error" if msg.get("severity", 1) == 2 else "warning",
            linter="eslint",
        )
        for file_item in data
        for msg in file_item.get("messages", [])
    ]
    return LinterResult(linter="eslint", available=True, violations=violations)


def _run_eslint(root: Path) -> LinterResult:
    """Run ``eslint`` or ``biome check`` on *root* and return a :class:`LinterResult`."""
    has_ts_js = any(root.rglob("*.ts")) or any(root.rglob("*.tsx")) or any(root.rglob("*.js"))
    if not has_ts_js:
        return LinterResult(linter="eslint", available=True)

    # Prefer biome if available (faster, zero-config)
    if shutil.which("biome"):
        return _run_biome(root)

    eslint_path = shutil.which("eslint")
    if eslint_path is None:
        # Try local node_modules/.bin/eslint
        local_eslint = root / "node_modules" / ".bin" / "eslint"
        if not local_eslint.is_file():
            return LinterResult(linter="eslint", available=False, error="eslint not found")
        eslint_cmd = str(local_eslint)
    else:
        eslint_cmd = eslint_path

    try:
        result = subprocess.run(
            [
                eslint_cmd,
                "--format",
                "json",
                "--no-eslintrc",
                "--parser-options=ecmaVersion:latest",
                "--ext",
                ".ts,.tsx,.js,.jsx,.mjs",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
            env={**os.environ, "NO_UPDATE_NOTIFIER": "1"},
        )
        return _parse_eslint_output(result.stdout.strip())

    except subprocess.TimeoutExpired:
        return LinterResult(linter="eslint", available=True, error="eslint timed out (>120s)")
    except OSError as exc:
        return LinterResult(linter="eslint", available=True, error=str(exc))


def _run_biome(root: Path) -> LinterResult:
    """Run ``biome check`` on *root* and return a :class:`LinterResult`."""
    biome_path = shutil.which("biome")
    if biome_path is None:
        return LinterResult(linter="eslint", available=False, error="biome not found in PATH")
    try:
        result = subprocess.run(
            [biome_path, "check", "--reporter=json", str(root)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=root,
        )
        raw = (result.stdout + result.stderr).strip()
        if not raw:
            return LinterResult(linter="eslint", available=True)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return LinterResult(linter="eslint", available=True)

        violations: list[LinterViolation] = []
        # biome JSON format: {"diagnostics": [...]}
        for diag in data.get("diagnostics", []):
            location = diag.get("location", {})
            span = location.get("span") or {}
            violations.append(
                LinterViolation(
                    file=Path(location.get("path", {}).get("file", "")),
                    line=span.get("start", {}).get("line", 0) + 1 if span else 0,
                    col=span.get("start", {}).get("character", 0) if span else 0,
                    code=diag.get("category", ""),
                    message=diag.get("description", ""),
                    severity="error" if diag.get("severity") == "error" else "warning",
                    linter="biome",
                )
            )
        return LinterResult(linter="eslint", available=True, violations=violations)

    except subprocess.TimeoutExpired:
        return LinterResult(linter="eslint", available=True, error="biome timed out (>60s)")
    except OSError as exc:
        return LinterResult(linter="eslint", available=True, error=str(exc))


# ─── Public API ───────────────────────────────────────────────────────────────

_LINTER_MAP: dict[str, Callable[..., LinterResult]] = {
    "ruff": _run_ruff,
    "mypy": _run_mypy,
    "eslint": _run_eslint,
}


def detect_linters(root: Path) -> list[str]:
    """Return the list of linters that are relevant for *root* based on file types."""
    linters: list[str] = []
    has_python = any(root.rglob("*.py"))
    has_ts_js = any(root.rglob("*.ts")) or any(root.rglob("*.tsx")) or any(root.rglob("*.js"))
    if has_python:
        linters.append("ruff")
        linters.append("mypy")
    if has_ts_js:
        linters.append("eslint")
    return linters


def run_linters(root: Path, linters: list[str] | None = None) -> list[LinterResult]:
    """Run the requested linters (or auto-detect) against *root*.

    Args:
        root: Project root directory to lint.
        linters: Explicit list of linter names (``"ruff"``, ``"mypy"``,
            ``"eslint"``).  Pass ``None`` to auto-detect based on file types.

    Returns:
        A :class:`LinterResult` per requested linter.  Unavailable linters
        are included with ``available=False`` so callers can report them.
    """
    if linters is None:
        linters = detect_linters(root)

    results: list[LinterResult] = []
    for name in linters:
        runner = _LINTER_MAP.get(name)
        if runner is None:
            results.append(LinterResult(linter=name, available=False, error=f"Unknown linter: {name!r}"))
            continue
        results.append(runner(root))
    return results
