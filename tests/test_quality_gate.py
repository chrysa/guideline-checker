"""The quality gate's metric parsers, against output pytest really emits.

The gate had no tests, and both parsers were reading the wrong number off real
runs: the test count came from a *test name*, and the coverage figure came from
pytest's progress marker. Neither was ever noticed, because the gate could not
record a baseline at all on a machine with a broken host interpreter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from quality_gate import QualityGate


@pytest.fixture
def gate() -> QualityGate:
    """A gate built without ``__init__``.

    The constructor loads ``.quality-gate.json`` and calls ``sys.exit`` when it is
    absent, which the test image legitimately is — the config selects *which*
    commands run, and these parsers only read their output.
    """
    return QualityGate.__new__(QualityGate)


# ─── test count ───────────────────────────────────────────────────────────────


def test_a_test_named_after_a_number_is_not_counted_as_the_total(gate: QualityGate) -> None:
    """The defect verbatim: a 785-test suite was recorded as 404.

    ``test_returns_none_on_404 PASSED`` satisfies ``(\\d+)\\s+passed``, and the
    threshold is ``>=``, so the baseline would have let the real count halve
    without complaint.
    """
    output = (
        "tests/test_gh_client.py::TestGhClientReadFile::test_returns_none_on_404 PASSED [ 43%]\n"
        "======================= 785 passed in 109.38s (0:01:49) ========================\n"
    )

    assert gate._parse_passed_tests(output) == 785


def test_trailing_build_noise_does_not_hide_the_summary(gate: QualityGate) -> None:
    """docker-test prints container lines after pytest's summary."""
    output = (
        "======================= 785 passed in 109.38s ========================\n"
        " Image guideline-checker-test Built\n"
        " Container guideline-checker-test-run-8531e Created\n"
    )

    assert gate._parse_passed_tests(output) == 785


def test_no_summary_reports_zero(gate: QualityGate) -> None:
    assert gate._parse_passed_tests("collection failed\n") == 0


# ─── coverage ─────────────────────────────────────────────────────────────────


def test_a_progress_marker_is_not_mistaken_for_coverage(gate: QualityGate) -> None:
    """The defect verbatim: 93% read off a progress line, for an 88.88% run.

    The test's own name contains "totals", which satisfied the loose token check.
    """
    output = (
        "tests/test_synthesis_html_reporter.py::test_synthesis_multiple_repos_totals PASSED [ 93%]\n"
        "Required test coverage of 85% reached. Total coverage: 88.88%\n"
    )

    assert gate._parse_coverage(output) == 88.88


def test_the_term_missing_total_row_is_read(gate: QualityGate) -> None:
    """A run without --cov-fail-under has no summary line, only the table."""
    output = "guideline_checker/cli.py   420    30    93%\nTOTAL   3833   410    89%\n"

    assert gate._parse_coverage(output) == 89.0


def test_absent_coverage_is_reported_as_missing_not_zero(gate: QualityGate) -> None:
    """-1.0 distinguishes "not measured" from "measured at 0%"."""
    assert gate._parse_coverage("nothing here\n") == -1.0
