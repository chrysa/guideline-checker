"""Atomic file writes — a reader never sees a half-written checkpoint, cache entry or report."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(*, path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file + ``os.replace`` (no torn files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
