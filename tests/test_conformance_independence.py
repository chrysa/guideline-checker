"""ADR D-0016 conformity: the engine is independent & pluggable.

Two of the ADR's three conformity criteria are executable here:

- (a) cloned into a third-party repo with **no** chrysa file — only its own
  ``CLAUDE.md`` — the engine still derives detectable rules from local prose.
- (c) removing the shipped ``guidelines/`` referential does not break the
  engine; only the per-repo derived cache is absent, and it is regenerable.

The kill-test in ADR D-0016 requires "no chrysa file" to still yield a non-empty
proven ruleset; this pins that behaviour so a regression can't silently make the
tool depend on a shipped referential again.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import run_checks
from guideline_checker.core.health import HealthState, compute_rule_health
from guideline_checker.guidelines import load_yaml_guidelines
from guideline_checker.loader import load_all_sources

# A third-party repo's own instructions — no chrysa standard, no guidelines/*.yml,
# no threshold value shipped by the tool. Plain prose the host already wrote.
_HOST_CLAUDE_MD = """\
# Project rules

- No print() calls in production code
- No bare except clauses
"""


def _third_party_repo(root: Path) -> None:
    (root / "CLAUDE.md").write_text(_HOST_CLAUDE_MD, encoding="utf-8")
    (root / "app.py").write_text("try:\n    run()\nexcept:\n    print('oops')\n", encoding="utf-8")


class TestIndependentPluggable:
    def test_no_shipped_referential_is_required(self, tmp_path: Path) -> None:
        # Criterion (c): with no guidelines/ dir, the YAML loader yields nothing
        # and raises nothing — the engine runs on host prose alone.
        _third_party_repo(tmp_path)
        assert not (tmp_path / "guidelines").exists()
        assert load_yaml_guidelines(tmp_path) == []

    def test_rules_derive_from_host_prose_alone(self, tmp_path: Path) -> None:
        # Criterion (a): a repo carrying only its own CLAUDE.md still produces a
        # non-empty proven ruleset — the rules trace to local prose, not chrysa.
        _third_party_repo(tmp_path)
        sources = load_all_sources(tmp_path)
        assert sources, "host CLAUDE.md must be discovered as a rule source"

        results = run_checks(tmp_path)
        health = compute_rule_health(sources, results)

        proven = [h for h in health if h.state is HealthState.PROVEN]
        assert proven, "expected at least one rule proven from host prose alone"
        # None of the proven rules depend on a shipped YAML detector.
        assert all(not h.has_declarative_detector for h in proven)
