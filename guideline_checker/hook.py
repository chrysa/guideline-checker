"""Pre-commit hook entry point for guideline-checker."""
from __future__ import annotations

import sys

from guideline_checker.cli import main

if __name__ == "__main__":
    sys.exit(main())
