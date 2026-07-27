"""Guard the setuptools-scm version single-sourcing (D-0019)."""

from __future__ import annotations

from importlib import metadata

import guideline_checker


def test_version_is_single_sourced_from_metadata() -> None:
    """__version__ mirrors the installed distribution metadata, not a hardcoded literal."""
    assert isinstance(guideline_checker.__version__, str)
    assert guideline_checker.__version__
    assert guideline_checker.__version__ == metadata.version("guideline-checker")
