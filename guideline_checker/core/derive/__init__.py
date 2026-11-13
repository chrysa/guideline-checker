"""Auto-derivation: turn a rule's prose into a detector without hand-authored YAML.

``derive_seed_rules`` (see :mod:`.seed`) is the heuristic-first step: a fast,
free, deterministic phrase-table lookup. Task 4/6 build the LLM-backed fallback
and the cached generation-loop pre-pass on top of this package.
"""

from __future__ import annotations

from guideline_checker.core.derive.seed import derive_seed_rules

__all__ = ["derive_seed_rules"]
