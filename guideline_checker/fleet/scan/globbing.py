"""Repository-path globs: ``*`` and ``?`` stay inside one segment, ``**`` crosses segments.

Implemented here rather than with ``PurePath.full_match`` (Python 3.13+) because the
package still supports Python 3.12.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

_CACHE_SIZE = 512


@lru_cache(maxsize=_CACHE_SIZE)
def compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a repository glob into an anchored regular expression."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_matches(*, pattern: str, paths: Iterable[str]) -> list[str]:
    """Sorted paths fully matching ``pattern``."""
    regex = compile_glob(pattern)
    return sorted(p for p in paths if regex.match(p))
