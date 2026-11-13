"""Scoring follows the SPEC formula; blocking failures are never hidden by an average."""

from __future__ import annotations

import pytest

from guideline_checker.fleet.scan.models import (
    CheckResult,
    CollectionError,
    Dimension,
    DimensionScore,
    ErrorKind,
    RepoOutcome,
    RepoResult,
    Severity,
    Status,
)
from guideline_checker.fleet.scan.scoring import aggregate, score_checks, score_repo


def c(
    status: Status, *, rule: str = "TST-001", dim: Dimension = Dimension.CONFORMANCE, blocking: bool = False
) -> CheckResult:
    return CheckResult(
        rule_id=rule,
        status=status,
        dimension=dim,
        severity=Severity.HIGH,
        blocking=blocking,
        message="m",
        error_kind=ErrorKind.NETWORK if status is Status.ERROR else None,
    )


def test_formula_score_coverage_denominator() -> None:
    checks = (
        [c(Status.PASS)] * 3
        + [c(Status.FAIL)]
        + [c(Status.UNKNOWN)] * 2
        + [c(Status.ERROR)]
        + [c(Status.NOT_APPLICABLE)]
    )
    score = score_checks(checks)
    assert score.score == pytest.approx(3 / 4)
    assert score.coverage == pytest.approx(4 / 7)
    assert score.applicable == 7
    assert score.not_applicable == 1


def test_unknown_and_error_never_count_as_pass() -> None:
    score = score_checks([c(Status.UNKNOWN), c(Status.ERROR)])
    assert score.score is None  # no decided check: no score, not 100%
    assert score.coverage == 0.0
    assert score_checks([c(Status.NOT_APPLICABLE)]).coverage is None


def test_blocking_failure_is_listed_whatever_the_score() -> None:
    checks = [c(Status.PASS, rule=f"OK-{i:03d}") for i in range(99)] + [c(Status.FAIL, rule="SEC-001", blocking=True)]
    score = score_checks(checks)
    assert score.score == pytest.approx(0.99)
    assert score.blocked is True
    assert score.blocking_failures == ["SEC-001"]
    not_blocking = score_checks([c(Status.UNKNOWN, rule="SEC-001", blocking=True)])
    assert not_blocking.blocked is False  # unknown is not a failure — but stays visible as unknown


def test_dimensions_are_never_merged() -> None:
    checks = [c(Status.PASS), c(Status.FAIL, dim=Dimension.OPERATIONAL_READINESS, blocking=True, rule="OPS-001")]
    scores, blocking = score_repo(checks)
    assert set(scores) == {Dimension.CONFORMANCE, Dimension.OPERATIONAL_READINESS}
    assert scores[Dimension.CONFORMANCE].score == 1.0
    assert scores[Dimension.OPERATIONAL_READINESS].score == 0.0
    assert blocking == ["OPS-001"]


def test_aggregate_pools_checks_and_keeps_errors() -> None:
    ok = RepoResult(repo="o/a", outcome=RepoOutcome.EVALUATED, checks=[c(Status.PASS), c(Status.FAIL)])
    blocked = RepoResult(
        repo="o/b",
        outcome=RepoOutcome.EVALUATED,
        checks=[c(Status.FAIL, blocking=True, rule="SEC-001")],
        blocked=True,
        blocking_failures=["SEC-001"],
    )
    broken = RepoResult(
        repo="o/c",
        outcome=RepoOutcome.ERROR,
        collection_errors=[CollectionError(kind=ErrorKind.NOT_FOUND, message="404")],
    )
    excluded = RepoResult(repo="o/d", outcome=RepoOutcome.EXCLUDED, exclusion_reason="archived")
    agg = aggregate([ok, blocked, broken, excluded])
    assert (agg.repos_requested, agg.repos_evaluated, agg.repos_errored, agg.repos_excluded) == (4, 2, 1, 1)
    assert agg.error_kinds == {ErrorKind.NOT_FOUND: 1}
    assert agg.blocked_repos == ["o/b"]
    conformance = agg.dimensions[Dimension.CONFORMANCE]
    assert (conformance.passed, conformance.fail) == (1, 2)
    assert agg.status_totals[Status.UNKNOWN] == 0


def test_dimension_score_validates_its_own_counts() -> None:
    with pytest.raises(ValueError, match="applicable must equal"):
        DimensionScore(score=None, coverage=None, passed=1, fail=0, unknown=0, error=0, not_applicable=0, applicable=5)


def test_pass_serialises_under_its_contract_name() -> None:
    dumped = score_checks([c(Status.PASS)]).model_dump(mode="json")
    assert dumped["pass"] == 1
    assert "passed" not in dumped
