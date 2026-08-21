"""Origin-side distribution-compliance checks.

File presence/equality checks (not per-line regex) emitted as standard ``Violation``s,
so every existing reporter and the web dashboard render them unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from guideline_checker.core.detection import Violation
from guideline_checker.core.detection.scanner_source import Scanner
from guideline_checker.fleet.manifest import RepoTarget

STANDARDS_PATH = ".chrysa/STANDARDS.md"
CLAUDE_PATH = "CLAUDE.md"
PRECOMMIT_PATH = ".pre-commit-config.yaml"
LICENSE_PATH = "LICENSE"

CHECK_IDS: tuple[str, ...] = ("standards-file", "claude-import", "precommit-pin", "license-present")


@dataclass(frozen=True)
class Expectations:
    canonical_standards: str
    license_text: str
    precommit_repo: str = "chrysa/pre-commit-tools"
    import_marker: str = "@.chrysa/STANDARDS.md"


def load_expectations(shared_standards_root: Path) -> Expectations:
    canonical = (shared_standards_root / "standards" / "STANDARDS.chrysa.md").read_text(encoding="utf-8")
    license_text = (shared_standards_root / "templates" / "LICENSE.mit").read_text(encoding="utf-8")
    return Expectations(canonical_standards=canonical, license_text=license_text)


def _violation(rel_path: str, check_id: str, message: str) -> Violation:
    return Violation(file=Path(rel_path), line_number=1, line_content=message, rule=check_id, severity="error")


def _check_standards(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(STANDARDS_PATH)
    if content == exp.canonical_standards:
        return None
    msg = "missing" if content is None else "differs from canonical STANDARDS.chrysa.md"
    return _violation(STANDARDS_PATH, "standards-file", f".chrysa/STANDARDS.md {msg}")


def _check_claude_import(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(CLAUDE_PATH)
    if content is not None and exp.import_marker in content:
        return None
    return _violation(CLAUDE_PATH, "claude-import", f"CLAUDE.md missing '{exp.import_marker}' import")


def _check_precommit(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(PRECOMMIT_PATH)
    if content is not None and exp.precommit_repo in content:
        return None
    return _violation(PRECOMMIT_PATH, "precommit-pin", f"pre-commit missing {exp.precommit_repo} pin")


def _check_license(scanner: Scanner, _exp: Expectations) -> Violation | None:
    if scanner.read_file(LICENSE_PATH) is not None:
        return None
    return _violation(LICENSE_PATH, "license-present", "LICENSE absent")


def audit(scanner: Scanner, target: RepoTarget, expected: Expectations) -> list[Violation]:
    violations: list[Violation | None] = []
    if target.standards_applicable:
        violations.append(_check_standards(scanner, expected))
        violations.append(_check_claude_import(scanner, expected))
    if target.precommit_applicable:
        violations.append(_check_precommit(scanner, expected))
    if target.license_applicable:
        violations.append(_check_license(scanner, expected))
    return [v for v in violations if v is not None]
