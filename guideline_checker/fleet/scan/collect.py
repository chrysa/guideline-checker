"""Per-repository evidence collection through the GitHub REST API — static inspection only.

Nothing is cloned and no repository code is executed. Only what the active rules (and
the revival signals, when enabled) need is fetched: :class:`CollectionNeeds` is derived
from the rule set first, then :class:`Collector` fetches exactly that. Each facet records
its own typed :class:`CollectionError`, so one forbidden file never hides the rest of the
evidence. A primary rate-limit abort (:class:`RateLimitedError`) propagates to the engine,
which stops collecting the remaining repositories.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypeVar
from urllib.parse import quote

from guideline_checker.fleet.scan.errors import GithubError, NotFoundError, RateLimitedError
from guideline_checker.fleet.scan.github import GithubClient
from guideline_checker.fleet.scan.models import (
    MAX_COMMITS_PER_PAGE,
    CiLastRunMethod,
    CollectionError,
    CommitMessagesAbsentMethod,
    ErrorKind,
    FileAbsentMethod,
    FileExistsMethod,
    FileRegexMethod,
    MakefileTargetsMethod,
    ProfileDef,
    RuleDef,
    TreeGlobMethod,
    utc_now,
)

DEFAULT_BRANCH = "default"
# Only push runs reflect the branch itself; schedule/dependabot/PR-helper workflows do not.
CI_EVENT = "push"
FileState = Literal["present", "missing", "too_large", "error"]
T = TypeVar("T")

_TREE_METHODS = (FileExistsMethod, FileAbsentMethod, TreeGlobMethod)


@dataclass(frozen=True)
class CollectionNeeds:
    """What a scan must fetch per repository, derived from the active rules."""

    tree: bool = False
    files: frozenset[str] = frozenset()
    ci_branches: frozenset[str] = frozenset()
    commits_n: int = 0
    tags: bool = False

    @classmethod
    def from_rules(cls, *, rules: Iterable[RuleDef], profiles: dict[str, ProfileDef], revival: bool) -> CollectionNeeds:
        tree = bool(profiles) or revival
        files: set[str] = set()
        branches: set[str] = {DEFAULT_BRANCH} if revival else set()
        commits_n = 0
        for rule in rules:
            method = rule.method
            if isinstance(method, _TREE_METHODS):
                tree = True
            elif isinstance(method, FileRegexMethod):
                files.update(method.params.candidates)
            elif isinstance(method, MakefileTargetsMethod):
                files.add(method.params.path)
            elif isinstance(method, CiLastRunMethod):
                branches.add(method.params.branch)
            elif isinstance(method, CommitMessagesAbsentMethod):
                commits_n = max(commits_n, method.params.last_n)
        return cls(
            tree=tree or bool(files),
            files=frozenset(files),
            ci_branches=frozenset(branches),
            commits_n=min(commits_n, MAX_COMMITS_PER_PAGE),
            tags=revival,
        )


@dataclass(frozen=True)
class Provenance:
    endpoint: str
    fetched_at: datetime


@dataclass(frozen=True)
class HeadCommit:
    sha: str
    committed_at: datetime | None
    provenance: Provenance


@dataclass(frozen=True)
class TreeSnapshot:
    """Blob paths (with sizes) and directory paths of the default-branch tree."""

    blobs: dict[str, int | None]
    dirs: frozenset[str]
    truncated: bool
    provenance: Provenance

    @property
    def all_paths(self) -> frozenset[str]:
        return frozenset(self.blobs) | self.dirs

    def has(self, path: str) -> bool:
        clean = path.rstrip("/")
        return clean in self.blobs or clean in self.dirs


@dataclass(frozen=True)
class FileContent:
    path: str
    state: FileState
    provenance: Provenance | None
    content: str | None = None
    size: int | None = None
    error: CollectionError | None = None


@dataclass(frozen=True)
class WorkflowRun:
    """Latest completed run on a branch; ``run is None`` means GitHub reports no completed run."""

    branch: str
    run: dict[str, Any] | None
    provenance: Provenance


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    message: str


@dataclass(frozen=True)
class RecentCommits:
    commits: list[CommitInfo]
    provenance: Provenance


@dataclass(frozen=True)
class TagsInfo:
    names: list[str]
    provenance: Provenance


@dataclass
class RepoEvidence:
    """Everything collected for one repository; a missing facet has an entry in ``errors``."""

    repo: str
    collected_at: datetime
    metadata: dict[str, Any] | None = None
    metadata_provenance: Provenance | None = None
    head: HeadCommit | None = None
    tree: TreeSnapshot | None = None
    files: dict[str, FileContent] = field(default_factory=dict)
    ci_runs: dict[str, WorkflowRun] = field(default_factory=dict)
    commits: RecentCommits | None = None
    tags: TagsInfo | None = None
    errors: dict[str, CollectionError] = field(default_factory=dict)

    @property
    def default_branch(self) -> str | None:
        branch = (self.metadata or {}).get("default_branch")
        return branch if isinstance(branch, str) else None

    @property
    def archived(self) -> bool:
        """Archived on GitHub: out of scope, so nothing past the metadata is fetched."""
        return (self.metadata or {}).get("archived") is True


def to_collection_error(exc: GithubError) -> CollectionError:
    return CollectionError(kind=exc.kind, message=str(exc), endpoint=exc.endpoint, status_code=exc.status_code)


class Collector:
    """Fetch the evidence one repository needs. Stateless per repo; safe to share across tasks."""

    def __init__(self, *, client: GithubClient, needs: CollectionNeeds, max_file_bytes: int) -> None:
        self._gh = client
        self._needs = needs
        self._max_bytes = max_file_bytes

    async def collect(self, repo: str) -> RepoEvidence:
        owner, name = repo.split("/", 1)
        base = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        evidence = RepoEvidence(repo=repo, collected_at=utc_now())
        meta = await self._facet(evidence=evidence, facet="metadata", call=self._gh.get_json(base))
        if meta is None or not isinstance(meta.data, dict):
            if meta is not None:
                evidence.errors["metadata"] = CollectionError(
                    kind=ErrorKind.INVALID_RESPONSE, message="repo is not an object"
                )
            return evidence
        evidence.metadata = meta.data
        evidence.metadata_provenance = Provenance(endpoint=meta.endpoint, fetched_at=meta.fetched_at)
        if evidence.archived:
            return evidence
        branch = evidence.default_branch or ""
        evidence.head = await self._head(evidence=evidence, base=base, branch=branch)
        ref = evidence.head.sha if evidence.head is not None else branch
        if self._needs.tree and ref:
            evidence.tree = await self._tree(evidence=evidence, base=base, ref=ref)
        if self._needs.files and ref:
            await self._files(evidence=evidence, base=base, ref=ref)
        for wanted in sorted(self._needs.ci_branches):
            actual = branch if wanted == DEFAULT_BRANCH else wanted
            run = await self._ci(evidence=evidence, base=base, wanted=wanted, branch=actual)
            if run is not None:
                evidence.ci_runs[wanted] = run
        if self._needs.commits_n and ref:
            evidence.commits = await self._commits(evidence=evidence, base=base, ref=ref)
        if self._needs.tags:
            evidence.tags = await self._tags(evidence=evidence, base=base)
        return evidence

    async def _facet(self, *, evidence: RepoEvidence, facet: str, call: Awaitable[T]) -> T | None:
        """Await one API call; record a typed error under ``facet`` (rate-limit abort propagates)."""
        try:
            return await call
        except RateLimitedError:
            raise
        except GithubError as exc:
            evidence.errors[facet] = to_collection_error(exc)
            return None

    async def _head(self, *, evidence: RepoEvidence, base: str, branch: str) -> HeadCommit | None:
        if not branch:
            evidence.errors["head"] = CollectionError(
                kind=ErrorKind.INVALID_RESPONSE, message="no default branch reported"
            )
            return None
        resp = await self._facet(
            evidence=evidence, facet="head", call=self._gh.get_json(f"{base}/commits/{quote(branch, safe='')}")
        )
        if resp is None:
            return None
        data = resp.data if isinstance(resp.data, dict) else {}
        sha = data.get("sha")
        if not isinstance(sha, str):
            evidence.errors["head"] = CollectionError(kind=ErrorKind.INVALID_RESPONSE, message="head commit has no sha")
            return None
        commit = data.get("commit") or {}
        stamp = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
        return HeadCommit(
            sha=sha,
            committed_at=parse_github_datetime(stamp),
            provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at),
        )

    async def _tree(self, *, evidence: RepoEvidence, base: str, ref: str) -> TreeSnapshot | None:
        resp = await self._facet(
            evidence=evidence,
            facet="tree",
            call=self._gh.get_json(f"{base}/git/trees/{quote(ref, safe='')}", params={"recursive": 1}),
        )
        if resp is None:
            return None
        data = resp.data if isinstance(resp.data, dict) else {}
        blobs: dict[str, int | None] = {}
        dirs: set[str] = set()
        for entry in data.get("tree") or []:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str):
                continue
            if entry.get("type") == "tree":
                dirs.add(path)
            elif entry.get("type") == "blob":
                size = entry.get("size")
                blobs[path] = size if isinstance(size, int) else None
        return TreeSnapshot(
            blobs=blobs,
            dirs=frozenset(dirs),
            truncated=bool(data.get("truncated")),
            provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at),
        )

    async def _files(self, *, evidence: RepoEvidence, base: str, ref: str) -> None:
        paths = sorted(self._needs.files)
        results = await asyncio.gather(*(self._file(evidence=evidence, base=base, ref=ref, path=p) for p in paths))
        evidence.files = dict(zip(paths, results, strict=True))

    async def _file(self, *, evidence: RepoEvidence, base: str, ref: str, path: str) -> FileContent:
        tree = evidence.tree
        if tree is not None and not tree.has(path) and not tree.truncated:
            return FileContent(path=path, state="missing", provenance=tree.provenance)
        known_size = tree.blobs.get(path) if tree is not None else None
        if known_size is not None and known_size > self._max_bytes:
            return FileContent(
                path=path, state="too_large", provenance=tree.provenance if tree else None, size=known_size
            )
        endpoint = f"{base}/contents/{quote(path, safe='/')}"
        try:
            resp = await self._gh.get_json(endpoint, params={"ref": ref})
        except RateLimitedError:
            raise
        except NotFoundError as exc:
            return FileContent(path=path, state="missing", provenance=_provenance_of(exc))
        except GithubError as exc:
            return FileContent(path=path, state="error", provenance=None, error=to_collection_error(exc))
        return self._decode(
            path=path, data=resp.data, provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at)
        )

    def _decode(self, *, path: str, data: Any, provenance: Provenance) -> FileContent:  # noqa: ANN401 - raw JSON
        if not isinstance(data, dict) or data.get("type") != "file":
            return FileContent(path=path, state="missing", provenance=provenance)
        size = data.get("size") if isinstance(data.get("size"), int) else None
        raw = data.get("content")
        if (
            (size is not None and size > self._max_bytes)
            or data.get("encoding") != "base64"
            or not isinstance(raw, str)
        ):
            return FileContent(path=path, state="too_large", provenance=provenance, size=size)
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            error = CollectionError(kind=ErrorKind.INVALID_RESPONSE, message="content is not valid base64")
            return FileContent(path=path, state="error", provenance=provenance, error=error)
        if len(blob) > self._max_bytes:
            return FileContent(path=path, state="too_large", provenance=provenance, size=len(blob))
        return FileContent(
            path=path,
            state="present",
            provenance=provenance,
            content=blob.decode("utf-8", errors="replace"),
            size=len(blob),
        )

    async def _ci(self, *, evidence: RepoEvidence, base: str, wanted: str, branch: str) -> WorkflowRun | None:
        resp = await self._facet(
            evidence=evidence,
            facet=f"ci:{wanted}",
            call=self._gh.get_json(
                f"{base}/actions/runs",
                params={"branch": branch, "event": CI_EVENT, "per_page": 1, "status": "completed"},
            ),
        )
        if resp is None:
            return None
        data = resp.data if isinstance(resp.data, dict) else {}
        runs = data.get("workflow_runs") or []
        run = runs[0] if runs and isinstance(runs[0], dict) else None
        return WorkflowRun(
            branch=branch, run=run, provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at)
        )

    async def _commits(self, *, evidence: RepoEvidence, base: str, ref: str) -> RecentCommits | None:
        resp = await self._facet(
            evidence=evidence,
            facet="commits",
            call=self._gh.get_json(f"{base}/commits", params={"sha": ref, "per_page": self._needs.commits_n}),
        )
        if resp is None:
            return None
        items = resp.data if isinstance(resp.data, list) else []
        commits = [
            CommitInfo(sha=str(item.get("sha", "")), message=str((item.get("commit") or {}).get("message", "")))
            for item in items
            if isinstance(item, dict)
        ]
        return RecentCommits(commits=commits, provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at))

    async def _tags(self, *, evidence: RepoEvidence, base: str) -> TagsInfo | None:
        resp = await self._facet(
            evidence=evidence, facet="tags", call=self._gh.get_json(f"{base}/tags", params={"per_page": 1})
        )
        if resp is None:
            return None
        items = resp.data if isinstance(resp.data, list) else []
        names = [str(t.get("name")) for t in items if isinstance(t, dict) and t.get("name")]
        return TagsInfo(names=names, provenance=Provenance(endpoint=resp.endpoint, fetched_at=resp.fetched_at))


CollectFn = Callable[[str], Awaitable[RepoEvidence]]


def _provenance_of(exc: GithubError) -> Provenance:
    return Provenance(endpoint=exc.endpoint or "", fetched_at=utc_now())


def parse_github_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
