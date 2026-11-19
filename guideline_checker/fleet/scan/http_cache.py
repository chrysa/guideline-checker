"""On-disk ETag cache for GitHub GET responses.

Entry files are named by a SHA-256 of the request identity, so a repository or path name
can never steer a write outside the cache directory. The identity includes a hash of the
token so that two tokens with different visibility never share entries; the token itself
is never written. Entries younger than ``ttl_s`` are served without a request; older
entries are revalidated with ``If-None-Match`` and refreshed on ``304``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guideline_checker.fleet.scan.atomic_io import atomic_write_text

_DIR_MODE = 0o700


@dataclass(frozen=True)
class CacheEntry:
    url: str
    etag: str | None
    fetched_at: float
    body: Any
    next_url: str | None

    def age_s(self, now: float) -> float:
        return now - self.fetched_at


class EtagCache:
    def __init__(self, *, directory: Path, ttl_s: int, clock: Callable[[], float], token: str | None) -> None:
        self._dir = directory
        self._ttl_s = ttl_s
        self._clock = clock
        self._auth_scope = hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def get(self, url: str) -> CacheEntry | None:
        path = self._path(url)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("url") != url:
            return None
        return CacheEntry(
            url=url,
            etag=raw.get("etag"),
            fetched_at=float(raw.get("fetched_at", 0.0)),
            body=raw.get("body"),
            next_url=raw.get("next_url"),
        )

    def is_fresh(self, entry: CacheEntry) -> bool:
        return entry.age_s(self._clock()) < self._ttl_s

    def put(self, *, url: str, etag: str | None, body: Any, next_url: str | None) -> CacheEntry:  # noqa: ANN401
        entry = CacheEntry(url=url, etag=etag, fetched_at=self._clock(), body=body, next_url=next_url)
        self._write(entry)
        return entry

    def touch(self, entry: CacheEntry) -> CacheEntry:
        """A ``304`` confirmed the entry: restart its TTL, keep its body."""
        refreshed = CacheEntry(
            url=entry.url, etag=entry.etag, fetched_at=self._clock(), body=entry.body, next_url=entry.next_url
        )
        self._write(refreshed)
        return refreshed

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(f"{self._auth_scope}\n{url}".encode()).hexdigest()
        return self._dir / f"{digest}.json"

    def _write(self, entry: CacheEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        self._dir.chmod(_DIR_MODE)  # mkdir leaves a pre-existing directory's mode untouched
        payload = {
            "url": entry.url,
            "etag": entry.etag,
            "fetched_at": entry.fetched_at,
            "body": entry.body,
            "next_url": entry.next_url,
        }
        atomic_write_text(path=self._path(entry.url), text=json.dumps(payload))
