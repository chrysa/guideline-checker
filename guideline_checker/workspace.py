"""Workspace discovery for the multi-project web workshop.

The web UI can point at any project in a workspace — the parent directory that
holds several sibling repos. ``discover_projects`` lists the immediate
sub-directories that are git repos carrying at least one rule source, so the
front can render a project selector and scan whichever one the user picks.
Filesystem-only, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# A repo needs at least one of these for guideline-checker to have rules to run.
_RULE_FILES = ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md", ".claude/CLAUDE.md")
_RULE_DIRS = (".github/instructions", "guidelines")


@dataclass(frozen=True)
class Project:
    """A discovered project the workshop can scan."""

    name: str
    path: str  # absolute filesystem path


def discover_projects(workspace: Path) -> list[Project]:
    """Return the sorted git-repo sub-directories of ``workspace`` that carry rules."""
    try:
        children = sorted(workspace.iterdir())
    except (OSError, NotADirectoryError):
        return []
    projects: list[Project] = []
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / ".git").exists() or not _has_rule_source(child):
            continue
        projects.append(Project(name=child.name, path=str(child.resolve())))
    return projects


def _has_rule_source(root: Path) -> bool:
    """True when the repo carries a markdown rule source or a rule directory."""
    return any((root / rel).is_file() for rel in _RULE_FILES) or any((root / rel).is_dir() for rel in _RULE_DIRS)
