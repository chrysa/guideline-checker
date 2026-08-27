"""File-access abstraction shared by the distribution audit.

``LocalScanner`` reads the working tree; ``OriginScanner`` reads ``origin/<default>``
via the ``gh`` API and is therefore immune to the stale-clone trap by construction.

``OriginScanner`` is structurally typed against ``_OriginClient`` rather than importing
``fleet.gh_client.GhClient`` directly — ``core/`` never depends on ``fleet/`` (any concrete
client, such as ``GhClient``, satisfies this protocol without core needing to import it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Scanner(Protocol):
    def read_file(self, rel_path: str) -> str | None: ...


class _OriginClient(Protocol):
    def default_branch(self, owner: str, repo: str) -> str: ...

    def read_file(self, owner: str, repo: str, path: str, ref: str) -> str | None: ...


class LocalScanner:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read_file(self, rel_path: str) -> str | None:
        try:
            return (self.root / rel_path).read_text(encoding="utf-8")
        except OSError:
            return None


class OriginScanner:
    def __init__(self, owner: str, repo: str, client: _OriginClient, ref: str | None = None) -> None:
        self.owner = owner
        self.repo = repo
        self._client = client
        self._ref = ref

    @property
    def ref(self) -> str:
        if self._ref is None:
            self._ref = self._client.default_branch(self.owner, self.repo)
        return self._ref

    def read_file(self, rel_path: str) -> str | None:
        return self._client.read_file(self.owner, self.repo, rel_path, self.ref)
