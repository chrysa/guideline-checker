"""Revival-candidate signals — observable foundations of a repository, **not** a maturity verdict.

Points reward foundations that make a dormant project cheap to restart (tests, CI history,
container files, a release/changelog, a substantive README). The last-commit age is
reported for context and used as a tie-breaker, never as points: a dormant but well-founded
repository is exactly what this list is meant to surface. Every candidate carries the
caveat :data:`REVIVAL_CAVEAT`.
"""

from __future__ import annotations

from datetime import datetime

from guideline_checker.fleet.scan.collect import DEFAULT_BRANCH, RepoEvidence
from guideline_checker.fleet.scan.globbing import glob_matches
from guideline_checker.fleet.scan.models import RepoOutcome, RepoResult, RevivalCandidate, RevivalSignal, Status

TEST_GLOBS = ("tests/**", "test/**", "**/test_*.py", "**/*_test.*", "**/*.test.*", "**/*.spec.*", "**/__tests__/**")
CONTAINER_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml")
CHANGELOG_FILES = ("CHANGELOG.md", "CHANGELOG", "CHANGELOG.rst", "HISTORY.md")
README_FILES = ("README.md", "README.rst", "README", "README.txt")
README_MIN_BYTES = 1000
POINTS = {"tests_present": 2.0, "ci_status": 2.0, "container": 1.0, "release_signal": 1.0, "readme_size": 1.0}
_CI_FAILED_POINTS = 1.0  # a failing CI still proves a pipeline exists


def revival_signals(*, evidence: RepoEvidence, now: datetime) -> list[RevivalSignal]:
    """Compute every signal; an undeterminable one is ``unknown`` and earns nothing."""
    return [
        _last_commit_age(evidence=evidence, now=now),
        _ci_status(evidence),
        _tree_signal(evidence, name="tests_present", globs=TEST_GLOBS),
        _tree_signal(evidence, name="container", globs=CONTAINER_FILES),
        _release_signal(evidence),
        _readme_size(evidence),
    ]


def rank_candidates(*, results: list[RepoResult], top_n: int) -> list[RevivalCandidate]:
    """Evaluated repositories ranked by signal points, then oldest head commit, then name."""
    scored = [
        (sum(s.points for s in r.revival_signals), _age(r), r)
        for r in results
        if r.outcome is RepoOutcome.EVALUATED and r.revival_signals
    ]
    scored.sort(key=_sort_key)
    return [
        RevivalCandidate(rank=i, repo=r.repo, project_id=r.project_id, signal_points=points, signals=r.revival_signals)
        for i, (points, _, r) in enumerate(scored[:top_n], start=1)
    ]


def _sort_key(item: tuple[float, int, RepoResult]) -> tuple[float, int, str]:
    points, age, result = item
    return (-points, -age, result.repo)


def _age(result: RepoResult) -> int:
    for signal in result.revival_signals:
        if signal.name == "last_commit_age" and isinstance(signal.value, int):
            return signal.value
    return -1


def _last_commit_age(*, evidence: RepoEvidence, now: datetime) -> RevivalSignal:
    head = evidence.head
    if head is None or head.committed_at is None:
        return RevivalSignal(name="last_commit_age", status=Status.UNKNOWN, detail="head commit date unavailable")
    days = (now - head.committed_at).days
    return RevivalSignal(
        name="last_commit_age", status=Status.PASS, value=days, detail=f"{days} day(s) since last commit"
    )


def _ci_status(evidence: RepoEvidence) -> RevivalSignal:
    run = evidence.ci_runs.get(DEFAULT_BRANCH)
    if run is None:
        return RevivalSignal(name="ci_status", status=Status.UNKNOWN, detail="workflow runs not collected")
    if run.run is None:
        return RevivalSignal(name="ci_status", status=Status.UNKNOWN, detail="no completed workflow run")
    conclusion = run.run.get("conclusion")
    if conclusion == "success":
        return RevivalSignal(name="ci_status", status=Status.PASS, value=conclusion, points=POINTS["ci_status"])
    return RevivalSignal(name="ci_status", status=Status.FAIL, value=str(conclusion), points=_CI_FAILED_POINTS)


def _tree_signal(evidence: RepoEvidence, *, name: str, globs: tuple[str, ...]) -> RevivalSignal:
    tree = evidence.tree
    if tree is None:
        return RevivalSignal(name=name, status=Status.UNKNOWN, detail="tree not collected")
    paths = tree.all_paths
    hits = sorted({m for g in globs for m in glob_matches(pattern=g, paths=paths)})
    if hits:
        return RevivalSignal(name=name, status=Status.PASS, value=len(hits), points=POINTS[name], detail=hits[0])
    status = Status.UNKNOWN if tree.truncated else Status.FAIL
    return RevivalSignal(name=name, status=status, value=0)


def _release_signal(evidence: RepoEvidence) -> RevivalSignal:
    tags = evidence.tags
    tree = evidence.tree
    if tags is not None and tags.names:
        return RevivalSignal(
            name="release_signal",
            status=Status.PASS,
            value=tags.names[0],
            points=POINTS["release_signal"],
            detail="tag",
        )
    if tree is not None and any(tree.has(p) for p in CHANGELOG_FILES):
        return RevivalSignal(
            name="release_signal", status=Status.PASS, value="changelog", points=POINTS["release_signal"]
        )
    if tags is None or tree is None or tree.truncated:
        return RevivalSignal(name="release_signal", status=Status.UNKNOWN, detail="tags or tree unavailable")
    return RevivalSignal(name="release_signal", status=Status.FAIL, detail="no tag, no changelog")


def _readme_size(evidence: RepoEvidence) -> RevivalSignal:
    tree = evidence.tree
    if tree is None:
        return RevivalSignal(name="readme_size", status=Status.UNKNOWN, detail="tree not collected")
    for path in README_FILES:
        size = tree.blobs.get(path)
        if size is not None:
            ok = size >= README_MIN_BYTES
            return RevivalSignal(
                name="readme_size",
                status=Status.PASS if ok else Status.FAIL,
                value=size,
                points=POINTS["readme_size"] if ok else 0.0,
                detail=path,
            )
    status = Status.UNKNOWN if tree.truncated else Status.FAIL
    return RevivalSignal(name="readme_size", status=status, value=0, detail="no README at root")
