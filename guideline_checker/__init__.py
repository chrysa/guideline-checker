"""guideline-checker: check project compliance against Copilot instruction rules."""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("guideline-checker")
except metadata.PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "0.0.0+unknown"
