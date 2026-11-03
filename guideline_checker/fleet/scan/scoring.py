"""Per-dimension scoring, exactly as the SPEC defines it.

``score = pass / (pass + fail)``; ``coverage = (pass + fail) / applicable`` with
``applicable = pass + fail + unknown + error``. ``unknown`` and ``error`` never count as
pass. A failed ``blocking`` rule sets ``blocked`` and is listed whatever the score — an
average can never hide it. Dimensions are never merged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from guideline_checker.fleet.scan.models import (
    Aggregates,
    CheckResult,
    Dimension,
    DimensionScore,
    ErrorKind,
    RepoOutcome,
    RepoResult,
    Status,
)


def score_checks(checks: Iterable[CheckResult]) -> DimensionScore:
    """Score one set of checks (one repo x one dimension, or a fleet aggregate)."""
    items = list(checks)
    counts = Counter(c.status for c in items)
    passed, failed = counts[Status.PASS], counts[Status.FAIL]
    applicable = passed + failed + counts[Status.UNKNOWN] + counts[Status.ERROR]
    decided = passed + failed
    blocking = sorted({c.rule_id for c in items if c.blocking and c.status is Status.FAIL})
    return DimensionScore(
        score=passed / decided if decided else None,
        coverage=decided / applicable if applicable else None,
        passed=passed,
        fail=failed,
        unknown=counts[Status.UNKNOWN],
        error=counts[Status.ERROR],
        not_applicable=counts[Status.NOT_APPLICABLE],
        applicable=applicable,
        blocked=bool(blocking),
        blocking_failures=blocking,
    )


def score_repo(checks: list[CheckResult]) -> tuple[dict[Dimension, DimensionScore], list[str]]:
    """Scores per dimension present in ``checks`` and the repo's blocking failures."""
    scores = {dim: score_checks(c for c in checks if c.dimension is dim) for dim in Dimension if _has(checks, dim)}
    blocking = sorted({rule for score in scores.values() for rule in score.blocking_failures})
    return scores, blocking


def aggregate(results: list[RepoResult]) -> Aggregates:
    """Fleet totals. Dimension aggregates pool the checks of every repo (micro-average)."""
    checks = [check for repo in results for check in repo.checks]
    status_totals = dict.fromkeys(Status, 0) | Counter(c.status for c in checks)
    error_kinds: Counter[ErrorKind] = Counter()
    for repo in results:
        error_kinds.update(e.kind for e in repo.collection_errors)
    outcomes = Counter(r.outcome for r in results)
    return Aggregates(
        repos_requested=len(results),
        repos_evaluated=outcomes[RepoOutcome.EVALUATED],
        repos_excluded=outcomes[RepoOutcome.EXCLUDED],
        repos_errored=outcomes[RepoOutcome.ERROR],
        status_totals=dict(status_totals),
        error_kinds=dict(error_kinds),
        dimensions={
            dim: score_checks(c for c in checks if c.dimension is dim) for dim in Dimension if _has(checks, dim)
        },
        blocked_repos=sorted(r.repo for r in results if r.blocked),
    )


def _has(checks: list[CheckResult], dim: Dimension) -> bool:
    return any(c.dimension is dim for c in checks)
