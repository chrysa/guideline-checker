"""Import-boundary conformance (spec: docs/superpowers/specs/2026-08-19-guideline-checker-redesign-design.md, §6).

core/ must never import workshop/ or fleet/ — satellites depend on core, never the reverse.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "guideline_checker"
_FORBIDDEN_PREFIXES = ("guideline_checker.workshop", "guideline_checker.fleet")


def _core_python_files() -> list[Path]:
    core_dir = _PACKAGE_ROOT / "core"
    if not core_dir.exists():
        return []
    return sorted(core_dir.rglob("*.py"))


def _imported_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("core_file", _core_python_files(), ids=lambda p: str(p.relative_to(_PACKAGE_ROOT)))
def test_core_module_does_not_import_workshop_or_fleet(core_file: Path) -> None:
    imported = _imported_modules(core_file.read_text(encoding="utf-8"))
    violations = [m for m in imported if m.startswith(_FORBIDDEN_PREFIXES)]
    assert not violations, f"{core_file} imports satellite module(s): {violations}"


def test_boundary_test_covers_at_least_one_core_file_once_core_is_populated() -> None:
    # Guards against a silent no-op: once Task 2 lands, this must find real files.
    # Skipped until core/ has content — flip to a hard assertion after Task 2.
    pass
