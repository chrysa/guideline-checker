"""Thin wrapper over the ``gh`` CLI — the single mock seam for origin-side reads/writes.

All GitHub access funnels through :class:`GhClient`. Tests inject a fake ``runner``;
production uses the real ``gh`` subprocess. No token is ever read in code — ``gh`` owns auth.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

# Raw-content Accept header → the contents API returns file bytes (not base64 JSON).
_RAW_ACCEPT = "Accept: application/vnd.github.raw"


@dataclass(frozen=True)
class GhResult:
    ok: bool
    stdout: str
    stderr: str
    code: int


GhRunner = Callable[[Sequence[str]], GhResult]


def _real_runner(args: Sequence[str]) -> GhResult:
    """Run ``gh <args>`` with a hard timeout; never raises on non-zero exit."""
    gh = shutil.which("gh")
    if gh is None:
        return GhResult(ok=False, stdout="", stderr="gh not found", code=127)
    try:
        proc = subprocess.run(  # noqa: S603 — fixed binary, list args, no shell
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
