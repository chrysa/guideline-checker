"""Tests for workspace discovery — the multi-project selector's backing data.

The web workshop can scan any project in a workspace, not just its own root.
``discover_projects`` finds the immediate sub-directories that are git repos
carrying at least one rule source (CLAUDE.md / AGENTS.md / instructions /
guidelines), so the UI can offer them in a selector.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.workspace import Project, discover_projects


def _repo(root: Path, name: str, *, rule_file: str | None = "CLAUDE.md", git: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if git:
        (d / ".git").mkdir()
    if rule_file:
        (d / rule_file).write_text("- Never use print\n", encoding="utf-8")
    return d


def test_discovers_repos_with_a_rule_source(tmp_path: Path) -> None:
    _repo(tmp_path, "alpha")
    _repo(tmp_path, "beta", rule_file="AGENTS.md")

    projects = discover_projects(tmp_path)

    names = {p.name for p in projects}
    assert names == {"alpha", "beta"}
    assert all(isinstance(p, Project) and Path(p.path).is_absolute() for p in projects)


def test_skips_non_git_dirs_and_rule_less_repos(tmp_path: Path) -> None:
    _repo(tmp_path, "with-rules")
    _repo(tmp_path, "no-git", git=False)  # not a repo
    _repo(tmp_path, "no-rules", rule_file=None)  # repo without a rule source

    names = {p.name for p in discover_projects(tmp_path)}

    assert names == {"with-rules"}


def test_discovers_instructions_and_guidelines_sources(tmp_path: Path) -> None:
    a = _repo(tmp_path, "has-instructions", rule_file=None)
    (a / ".github" / "instructions").mkdir(parents=True)
    (a / ".github" / "instructions" / "py.instructions.md").write_text("- x\n", encoding="utf-8")
    b = _repo(tmp_path, "has-guidelines", rule_file=None)
    (b / "guidelines").mkdir()

    names = {p.name for p in discover_projects(tmp_path)}

    assert names == {"has-instructions", "has-guidelines"}


def test_results_are_sorted_and_skip_hidden(tmp_path: Path) -> None:
    _repo(tmp_path, "zulu")
    _repo(tmp_path, "alpha")
    _repo(tmp_path, ".hidden")

    projects = discover_projects(tmp_path)

    assert [p.name for p in projects] == ["alpha", "zulu"]


def test_missing_workspace_yields_no_projects(tmp_path: Path) -> None:
    assert discover_projects(tmp_path / "does-not-exist") == []
