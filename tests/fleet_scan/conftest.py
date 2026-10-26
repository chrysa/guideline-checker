"""Shared fixtures: a fixture config dir and an in-memory fake GitHub (httpx.MockTransport).

No test touches the network: every HTTP call goes through ``FakeGithub.transport``.
"""

from __future__ import annotations

import base64
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from guideline_checker.fleet.scan.config import EffectiveConfig, load_config
from guideline_checker.fleet.scan.github import GithubClient

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "config"
API = "https://api.github.test"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@dataclass
class FakeRepo:
    metadata: dict[str, Any]
    sha: str = "a" * 40
    committed_at: str = "2026-10-01T00:00:00Z"
    files: dict[str, str] = field(default_factory=dict)
    dirs: list[str] = field(default_factory=list)
    truncated: bool = False
    runs: list[dict[str, Any]] = field(default_factory=list)
    commits: list[dict[str, Any]] = field(default_factory=list)
    compare_commits: list[dict[str, Any]] = field(default_factory=list)  # a fork's own commits, oldest first
    tags: list[dict[str, Any]] = field(default_factory=list)
    status: int | None = None  # force every call for this repo to this HTTP status


def make_repo(name: str, **kwargs: Any) -> FakeRepo:
    metadata = {"full_name": name, "default_branch": "main", "archived": False, "visibility": "public"}
    metadata.update(kwargs.pop("metadata", {}))
    return FakeRepo(metadata=metadata, **kwargs)


class FakeGithub:
    """Routes the REST endpoints the collector uses onto :class:`FakeRepo` objects."""

    def __init__(self) -> None:
        self.repos: dict[str, FakeRepo] = {}
        self.calls: list[str] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        parts = unquote(request.url.path).split("/")
        if len(parts) < 4 or parts[1] != "repos":
            return httpx.Response(404, json={"message": "Not Found"})
        name = f"{parts[2]}/{parts[3]}"
        repo = self.repos.get(name)
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if repo.status is not None:
            return httpx.Response(repo.status, json={"message": "forced"})
        rest = parts[4:]
        return self._route(repo=repo, rest=rest, request=request)

    def _route(self, *, repo: FakeRepo, rest: list[str], request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        if not rest:
            return httpx.Response(200, json=repo.metadata)
        head = rest[0]
        if head == "commits" and len(rest) == 2:
            return httpx.Response(200, json={"sha": repo.sha, "commit": {"committer": {"date": repo.committed_at}}})
        if head == "compare":
            return httpx.Response(200, json={"commits": repo.compare_commits})
        if head == "commits":
            per_page = int(request.url.params.get("per_page", "30"))
            return httpx.Response(200, json=repo.commits[:per_page])
        if head == "git" and rest[1] == "trees":
            tree = [{"path": p, "type": "blob", "size": len(c.encode())} for p, c in repo.files.items()]
            tree += [{"path": d, "type": "tree"} for d in repo.dirs]
            return httpx.Response(200, json={"sha": repo.sha, "tree": tree, "truncated": repo.truncated})
        if head == "contents":
            path = "/".join(rest[1:])
            if path not in repo.files:
                return httpx.Response(404, json={"message": "Not Found"})
            raw = repo.files[path].encode()
            body = {"type": "file", "size": len(raw), "encoding": "base64", "content": base64.b64encode(raw).decode()}
            return httpx.Response(200, json=body)
        if head == "actions":
            return httpx.Response(200, json={"total_count": len(repo.runs), "workflow_runs": repo.runs[:1]})
        if head == "tags":
            return httpx.Response(200, json=repo.tags[:1])
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def fixture_config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(FIXTURE_CONFIG, target)
    return target


@pytest.fixture
def config(fixture_config_dir: Path) -> EffectiveConfig:
    return load_config(fixture_config_dir)


@pytest.fixture
def fake_github() -> FakeGithub:
    fake = FakeGithub()
    fake.repos["chrysa/alpha"] = make_repo(
        "chrysa/alpha",
        files={
            "README.md": "# alpha\n" + "x" * 1200,
            "pyproject.toml": "[project]\nname='alpha'\n[tool.ruff]\nline-length=120\n",
            "Makefile": "help: ## help\n\t@echo\ntest:\n\tpytest\n",
            "tests/unit/test_core.py": "def test_x():\n    assert True\n",
            "Dockerfile": "FROM python:3.14-slim\n",
            "CHANGELOG.md": "# Changelog\n",
        },
        dirs=["tests", "tests/unit"],
        runs=[{"id": 1, "conclusion": "success", "updated_at": "2026-10-20T00:00:00Z", "name": "CI"}],
        commits=[{"sha": "c1", "commit": {"message": "feat: x"}}, {"sha": "c2", "commit": {"message": "fix: y"}}],
        tags=[{"name": "v1.0.0"}],
    )
    fake.repos["chrysa/beta"] = make_repo(
        "chrysa/beta",
        committed_at="2024-01-01T00:00:00Z",
        files={"README.md": "short", ".env": "SECRET=1", "pyproject.toml": "[project]\n"},
        runs=[{"id": 2, "conclusion": "failure", "updated_at": "2024-01-02T00:00:00Z"}],
        commits=[{"sha": "w1", "commit": {"message": "WIP do not merge"}}],
    )
    fake.repos["chrysa/delta"] = make_repo("chrysa/delta", files={"package.json": "{}"})
    return fake


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def make_client(config: EffectiveConfig, fake_github: FakeGithub, tmp_path: Path):
    def factory(**overrides: Any) -> GithubClient:
        kwargs: dict[str, Any] = {
            "config": config.github,
            "token": None,
            "transport": fake_github.transport,
            "cache_root": tmp_path,
            "sleep": no_sleep,
        }
        kwargs.update(overrides)
        return GithubClient(**kwargs)

    return factory
