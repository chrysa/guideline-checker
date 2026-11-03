"""Repository lifecycle checks — is this repo alive, and what does it drag along?

The rest of the tool judges what is *inside* files: rules written in the host
repo, matched against lines. This module judges the repository as an object:
whether it is still alive, and what it carries that earns nothing.

Two questions, deliberately separate:

- **Is it dead?** No commit for a long time, an archive marker in its name, a
  deprecation notice in its README, or a path that no longer exists on disk.
  The answer is a *review candidate*, never an action.
- **What does a living repo drag along?** Caches, virtualenvs and build output
  committed by accident; worktrees left behind by agents; scratch files at the
  root.

Three safety rules, none negotiable:

1. **Nothing is deleted here.** Every finding is a ``Violation`` for a human to
   read. Archiving or removing anything is a separate, explicit gesture.
2. **A dirty repository is never proposed for deletion.** Uncommitted changes may
   hold unsaved work, so the score is pushed down rather than up.
3. **What costs is the versioning, not the bytes.** The fix for a committed
   cache is to stop tracking it, which leaves the files on disk and stays
   reversible.

Ported from the dissolved ``floating-knowledge-architect-offline`` repository
(decision D-025), where these checks had no CI, no baseline and no SARIF output.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from guideline_checker.core.detection import Violation

CHECK_IDS: tuple[str, ...] = (
    "repo-inactive",
    "repo-deprecated",
    "repo-missing",
    "tracked-artefact",
    "abandoned-worktree",
    "scratch-file",
)

DEFAULT_STALE_AFTER_DAYS = 365

_NAME_MARKERS = re.compile(
    r"(?:^|[-_. ])(?:old|legacy|archive|archived|obsolete|deprecated|backup|copy|v0)(?:$|[-_. ])",
    re.IGNORECASE,
)
_TEXT_MARKERS = re.compile(
    r"\b(?:deprecated|obsolete|archived|no longer maintained|non maintenu|"
    r"deprecie|obsolete|archive)\b",
    re.IGNORECASE,
)

# Directories that should never be tracked. The value is the reason shown to the
# reader: a cleanup without a justification is not auditable.
_TRACKED_ARTEFACTS: dict[str, str] = {
    "__pycache__": "Python bytecode, regenerated on every run",
    ".venv": "virtualenv, rebuilt by the install step",
    "node_modules": "dependencies installable from the manifest",
    ".mypy_cache": "type-check cache",
    ".ruff_cache": "lint cache",
    ".pytest_cache": "test cache",
    "dist": "rebuildable build output",
    "build": "rebuildable build output",
}

_SCRATCH_PREFIXES = ("scratch_", "tmp_", "temp_", "audit_")
_SCRATCH_SUFFIXES = (".orig", ".rej", ".bak", ".swp", ".tmp")


@dataclass(frozen=True)
class LifecycleReport:
    """What the checks concluded about one repository."""

    path: Path
    exists: bool
    dirty: bool
    inactive_days: int | None
    violations: list[Violation]


def _git(path: Path, *args: str) -> str | None:
    """Run git, returning None on any failure rather than raising."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        # Safe subprocess: fixed binary resolved via shutil.which, list args, no shell.
        result = subprocess.run(
            [git, "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _violation(relative: str, message: str, check_id: str, severity: str) -> Violation:
    return Violation(
        file=Path(relative),
        line_number=1,
        line_content=message,
        rule=check_id,
        severity=severity,
    )


def _inactive_days(path: Path) -> int | None:
    raw = _git(path, "log", "-1", "--format=%cI")
    if not raw:
        return None
    try:
        committed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return max(0, (datetime.now(UTC) - committed.astimezone(UTC)).days)


def _deprecation_marker(path: Path) -> str | None:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = path / name
        if not readme.is_file():
            continue
        match = _TEXT_MARKERS.search(readme.read_text(encoding="utf-8", errors="replace")[:25000])
        if match:
            return match.group(0)
    return None


def check_liveness(path: Path, *, stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> list[Violation]:
    """Is this repository still alive? Answers with review candidates, not actions."""
    name = path.name
    if not path.exists():
        return [_violation(name, "the local path no longer exists", "repo-missing", "warning")]

    found: list[Violation] = []
    if _NAME_MARKERS.search(name):
        found.append(_violation(name, "the name carries an age or archive marker", "repo-deprecated", "info"))
    marker = _deprecation_marker(path)
    if marker:
        found.append(
            _violation(
                name,
                f"the documentation carries the marker {marker!r}",
                "repo-deprecated",
                "warning",
            )
        )
    days = _inactive_days(path)
    if days is not None and days >= stale_after_days:
        found.append(_violation(name, f"no commit for {days} days", "repo-inactive", "warning"))
    return found


def check_tracked_artefacts(path: Path) -> list[Violation]:
    """Caches and build output committed by accident.

    They grow the history permanently and pollute every diff. The fix is to stop
    tracking them, not to delete them.
    """
    listing = _git(path, "ls-files")
    if not listing:
        return []
    offenders: dict[str, str] = {}
    for line in listing.splitlines():
        parts = Path(line).parts
        for marker, reason in _TRACKED_ARTEFACTS.items():
            if marker in parts:
                # Report the offending directory, not each file: proposing four
                # thousand removals would be unusable.
                index = parts.index(marker)
                offenders.setdefault(str(Path(*parts[: index + 1])), reason)
    return [
        _violation(
            relative,
            f"tracked by git although it is {reason} — untrack it, the files stay on disk",
            "tracked-artefact",
            "error",
        )
        for relative, reason in sorted(offenders.items())
    ]


def check_worktrees(path: Path) -> list[Violation]:
    """Worktrees registered but gone, or left behind by an agent."""
    listing = _git(path, "worktree", "list", "--porcelain")
    if not listing:
        return []
    main = path.resolve()
    found: list[Violation] = []
    for block in listing.split("\n\n"):
        line = next((row for row in block.splitlines() if row.startswith("worktree ")), None)
        if not line:
            continue
        location = Path(line.removeprefix("worktree ").strip())
        if location.resolve() == main:
            continue
        if not location.exists():
            found.append(
                _violation(
                    str(location),
                    "registered as a worktree but absent from disk — a dead reference",
                    "abandoned-worktree",
                    "warning",
                )
            )
        elif ".claude" in location.parts:
            found.append(
                _violation(
                    str(location),
                    "agent worktree kept after use — never reclaimed automatically",
                    "abandoned-worktree",
                    "warning",
                )
            )
    return found


def check_scratch_files(path: Path) -> list[Violation]:
    """Working files left at the repository root."""
    if not path.is_dir():
        return []
    found: list[Violation] = []
    for entry in sorted(path.iterdir()):
        name = entry.name
        if entry.is_dir() or name.startswith("."):
            continue
        if name.startswith(_SCRATCH_PREFIXES) or name.endswith(_SCRATCH_SUFFIXES):
            found.append(_violation(name, "working file left at the repository root", "scratch-file", "info"))
    return found


def audit(path: Path, *, stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> LifecycleReport:
    """Full lifecycle audit of one repository. Reads only, writes nothing."""
    if not path.exists():
        return LifecycleReport(
            path=path,
            exists=False,
            dirty=False,
            inactive_days=None,
            violations=check_liveness(path, stale_after_days=stale_after_days),
        )

    dirty = bool(_git(path, "status", "--porcelain"))
    violations = check_liveness(path, stale_after_days=stale_after_days)
    if dirty:
        # Uncommitted work may be unsaved: report, but never let the repo read as
        # safe to remove.
        violations.append(
            _violation(
                path.name,
                "uncommitted changes — cleanup and archiving are suspended",
                "repo-inactive",
                "info",
            )
        )
    else:
        violations += check_tracked_artefacts(path)
        violations += check_worktrees(path)
        violations += check_scratch_files(path)

    return LifecycleReport(
        path=path,
        exists=True,
        dirty=dirty,
        inactive_days=_inactive_days(path),
        violations=violations,
    )
