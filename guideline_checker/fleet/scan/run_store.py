"""Run directory: ``manifest.json`` + one atomically written ``repos/<owner>__<name>.json`` per repo.

File names are derived from validated identifiers only. A run id must match
:data:`RUN_ID_PATTERN`; a repository name that is not a valid GitHub ``owner/name`` (for
instance a typo passed with ``--repos``) is stored under a hash, so user input can never
traverse out of the run directory.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from guideline_checker.fleet.scan.atomic_io import atomic_write_text
from guideline_checker.fleet.scan.errors import ResumeRefusedError
from guideline_checker.fleet.scan.models import REPO_FULL_NAME_PATTERN, RepoResult, RunManifest

logger = logging.getLogger("guideline_checker.fleet.scan.run_store")

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_REPO_NAME = re.compile(REPO_FULL_NAME_PATTERN)
MANIFEST_FILE = "manifest.json"
REPOS_DIR = "repos"


def new_run_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def repo_file_name(repo: str) -> str:
    """``owner__name.json`` for a valid full name, ``invalid__<sha>.json`` otherwise."""
    if _REPO_NAME.fullmatch(repo) and repo.split("/", 1)[1] not in {".", ".."}:
        owner, name = repo.lower().split("/", 1)
        return f"{owner}__{name}.json"
    return f"invalid__{hashlib.sha256(repo.encode('utf-8')).hexdigest()[:16]}.json"


class RunStore:
    def __init__(self, *, output_dir: Path, run_id: str) -> None:
        if not RUN_ID_PATTERN.match(run_id):
            raise ResumeRefusedError(f"invalid run id {run_id!r}")
        self.run_id = run_id
        self.run_dir = output_dir / run_id
        self._repos_dir = self.run_dir / REPOS_DIR

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / MANIFEST_FILE

    def exists(self) -> bool:
        return self.manifest_path.is_file()

    def write_manifest(self, manifest: RunManifest) -> None:
        atomic_write_text(path=self.manifest_path, text=manifest.model_dump_json(indent=2))

    def read_manifest(self) -> RunManifest:
        if not self.exists():
            raise ResumeRefusedError(f"run {self.run_id} not found under {self.run_dir.parent}")
        try:
            return RunManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as exc:
            raise ResumeRefusedError(f"run {self.run_id}: unreadable manifest ({exc})") from exc

    def repo_path(self, repo: str) -> Path:
        path = self._repos_dir / repo_file_name(repo)
        if path.parent != self._repos_dir:  # pragma: no cover - defence in depth, names are pre-sanitised
            raise ResumeRefusedError(f"refusing repo file outside the run dir: {repo!r}")
        return path

    def write_repo(self, result: RepoResult) -> None:
        atomic_write_text(path=self.repo_path(result.repo), text=result.model_dump_json(indent=2))

    def load_repos(self, requested: list[str]) -> dict[str, RepoResult]:
        """Saved results of ``requested`` repos; an unreadable file is skipped (re-collected)."""
        loaded: dict[str, RepoResult] = {}
        for repo in requested:
            path = self.repo_path(repo)
            if not path.is_file():
                continue
            try:
                result = RepoResult.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError):
                logger.warning("checkpoint %s unreadable; repo will be collected again", path.name)
                continue
            if result.repo == repo:
                loaded[repo] = result
        return loaded

    def write_text(self, *, filename: str, text: str) -> Path:
        path = self.run_dir / filename
        atomic_write_text(path=path, text=text)
        return path
