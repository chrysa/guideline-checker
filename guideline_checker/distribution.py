"""Origin-side distribution-compliance checks.

File presence/equality checks (not per-line regex) emitted as standard ``Violation``s,
so every existing reporter and the web dashboard render them unchanged.

Standards are inlined into each repo's ``CLAUDE.md`` inside a managed block delimited by
``chrysa:standards:start``/``:end``. The canonical body is ``STANDARDS.chrysa.md`` minus
its leading HTML header comment (mirrors ``distribute-standards.sh``). There is no vendored
``.chrysa/STANDARDS.md`` and no ``@import`` any more.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from guideline_checker.checker import Violation
from guideline_checker.manifest import RepoTarget
from guideline_checker.scanner_source import Scanner

CLAUDE_PATH = "CLAUDE.md"
PRECOMMIT_PATH = ".pre-commit-config.yaml"
LICENSE_PATH = "LICENSE"

STANDARDS_BLOCK_START = "<!-- chrysa:standards:start · managed by distribute-standards.sh · DO NOT EDIT -->"
STANDARDS_BLOCK_END = "<!-- chrysa:standards:end -->"

CHECK_IDS: tuple[str, ...] = ("standards-block", "precommit-pin", "license-present")


@dataclass(frozen=True)
class Expectations:
    canonical_standards: str
    license_text: str
    precommit_repo: str = "chrysa/pre-commit-tools"


def load_expectations(shared_standards_root: Path) -> Expectations:
    canonical = (shared_standards_root / "standards" / "STANDARDS.chrysa.md").read_text(encoding="utf-8")
    license_text = (shared_standards_root / "templates" / "LICENSE.mit").read_text(encoding="utf-8")
    return Expectations(canonical_standards=canonical, license_text=license_text)


def standards_body(canonical: str) -> str:
    """Canonical text minus its leading HTML header comment — the managed block body."""
    lines = canonical.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines) and lines[idx].lstrip().startswith("<!--"):
        while idx < len(lines):
            closed = lines[idx].rstrip().endswith("-->")
            idx += 1
            if closed:
                break
    return "\n".join(lines[idx:]).strip()


def render_standards_block(exp: Expectations) -> str:
    """The full managed block (markers + body) as it should appear in CLAUDE.md."""
    return f"{STANDARDS_BLOCK_START}\n{standards_body(exp.canonical_standards)}\n{STANDARDS_BLOCK_END}\n"


def inject_standards_block(claude_content: str | None, exp: Expectations) -> str:
    """Insert or refresh the managed block in ``claude_content`` (remediation helper)."""
    block = render_standards_block(exp)
    if claude_content is None:
        return block
    start = claude_content.find(STANDARDS_BLOCK_START)
    end = claude_content.find(STANDARDS_BLOCK_END, start + 1) if start >= 0 else -1
    if start >= 0 and end >= 0:
        tail = claude_content[end + len(STANDARDS_BLOCK_END) :]
        return claude_content[:start] + block.rstrip("\n") + tail
    separator = "" if claude_content.endswith("\n") else "\n"
    return f"{claude_content}{separator}\n{block}"


def _extract_block(content: str) -> str | None:
    start = content.find(STANDARDS_BLOCK_START)
    if start < 0:
        return None
    body_start = start + len(STANDARDS_BLOCK_START)
    end = content.find(STANDARDS_BLOCK_END, body_start)
    if end < 0:
        return None
    return content[body_start:end]


def _violation(rel_path: str, check_id: str, message: str) -> Violation:
    return Violation(file=Path(rel_path), line_number=1, line_content=message, rule=check_id, severity="error")


def _check_standards_block(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(CLAUDE_PATH)
    if content is None:
        return _violation(CLAUDE_PATH, "standards-block", "CLAUDE.md missing")
    block = _extract_block(content)
    if block is None:
        return _violation(CLAUDE_PATH, "standards-block", "CLAUDE.md missing managed chrysa:standards block")
    if block.strip() != standards_body(exp.canonical_standards):
        msg = "chrysa:standards block differs from canonical STANDARDS.chrysa.md"
        return _violation(CLAUDE_PATH, "standards-block", msg)
    return None


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
        violations.append(_check_standards_block(scanner, expected))
    if target.precommit_applicable:
        violations.append(_check_precommit(scanner, expected))
    if target.license_applicable:
        violations.append(_check_license(scanner, expected))
    return [v for v in violations if v is not None]
