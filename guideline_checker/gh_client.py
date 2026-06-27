"""Thin wrapper over the ``gh`` CLI — the single mock seam for origin-side reads/writes.

All GitHub access funnels through :class:`GhClient`. Tests inject a fake ``runner``;
production uses the real ``gh`` subprocess. No token is ever read in code — ``gh`` owns auth.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Raw-content Accept header → the contents API returns file bytes (not base64 JSON).
_RAW_ACCEPT = "Accept: application/vnd.github.raw"


@dataclass(frozen=True)
class GhResult:
    ok: bool
    stdout: str
    stderr: str
    code: int


GhRunner = Callable[[Sequence[str]], GhResult]


def _real_runner(args: Sequence[str]) -> GhResult:  # pragma: no cover - real subprocess I/O boundary
    """Run ``gh <args>`` with a hard timeout; never raises on non-zero exit."""
    gh = shutil.which("gh")
    if gh is None:
        return GhResult(ok=False, stdout="", stderr="gh not found", code=127)
    try:
        # Safe subprocess: fixed binary resolved via shutil.which, list args, no shell.
        proc = subprocess.run(
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GhResult(ok=False, stdout="", stderr=str(exc), code=1)
    return GhResult(ok=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr, code=proc.returncode)


class GhClient:
    def __init__(self, runner: GhRunner | None = None) -> None:
        self._run: GhRunner = runner or _real_runner

    def available(self) -> bool:
        return shutil.which("gh") is not None

    def read_file(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        result = self._run(["api", "-H", _RAW_ACCEPT, f"repos/{owner}/{repo}/contents/{path}?ref={ref}"])
        return result.stdout if result.ok else None

    def default_branch(self, owner: str, repo: str) -> str:
        result = self._run(["api", f"repos/{owner}/{repo}", "--jq", ".default_branch"])
        return result.stdout.strip() if result.ok else "main"

    def repo_exists(self, owner: str, repo: str) -> bool:
        return self._run(["api", f"repos/{owner}/{repo}", "--jq", ".name"]).ok

    def branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        r = self._run(["api", f"repos/{owner}/{repo}/git/ref/heads/{branch}", "--jq", ".object.sha"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def create_branch(self, owner: str, repo: str, new_branch: str, from_sha: str) -> bool:
        return self._run(
            [
                "api",
                "--method",
                "POST",
                f"repos/{owner}/{repo}/git/refs",
                "-f",
                f"ref=refs/heads/{new_branch}",
                "-f",
                f"sha={from_sha}",
            ]
        ).ok

    def put_file(self, owner: str, repo: str, path: str, content: str, message: str, branch: str) -> bool:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        args = [
            "api",
            "--method",
            "PUT",
            f"repos/{owner}/{repo}/contents/{path}",
            "-f",
            f"message={message}",
            "-f",
            f"content={encoded}",
            "-f",
            f"branch={branch}",
        ]
        existing_sha = self._content_sha(owner, repo, path, branch)
        if existing_sha is not None:
            args += ["-f", f"sha={existing_sha}"]
        return self._run(args).ok

    def _content_sha(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        r = self._run(["api", f"repos/{owner}/{repo}/contents/{path}?ref={ref}", "--jq", ".sha"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def open_pr(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> str | None:
        r = self._run(
            [
                "pr",
                "create",
                "--repo",
                f"{owner}/{repo}",
                "--head",
                head,
                "--base",
                base,
                "--title",
                title,
                "--body",
                body,
            ]
        )
        return r.stdout.strip() if r.ok else None

    def find_pr(self, owner: str, repo: str, head: str) -> str | None:
        r = self._run(
            [
                "pr",
                "list",
                "--repo",
                f"{owner}/{repo}",
                "--head",
                head,
                "--state",
                "open",
                "--json",
                "url",
                "--jq",
                ".[0].url",
            ]
        )
        url = r.stdout.strip()
        return url if (r.ok and url) else None
