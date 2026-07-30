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

from quality_gate import CommandSpec, QualityGate


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


# ─── the baseline artefact ────────────────────────────────────────────────────


def test_the_baseline_record_drops_the_captured_output(gate: QualityGate) -> None:
    """The baseline is committed, diffed and read; a transcript belongs elsewhere.

    Keeping ``output`` made the file 344 KB, of which roughly 120 KB was a full
    detect-secrets dump. Only ``metric`` is ever read back.
    """
    result = {
        "gate": "Tests",
        "command": "make docker-test",
        "exit_code": 0,
        "metric_name": "passed_tests",
        "metric": 791,
        "timestamp": "2026-07-29T12:00:00",
        "output": "x" * 120_000,
    }

    record = gate._for_baseline(result)

    assert "output" not in record
    assert record["metric"] == 791  # what verify compares against survives


def test_the_baseline_record_keeps_its_provenance(gate: QualityGate) -> None:
    """Which command produced the number, and when, is worth a few bytes."""
    record = gate._for_baseline(
        {"gate": "Lint", "command": "make lint", "exit_code": 0, "metric": 0, "timestamp": "t", "output": "noise"}
    )

    assert record["command"] == "make lint"
    assert record["timestamp"] == "t"


# ─── the comparison that makes a baseline mean something ──────────────────────


@pytest.mark.parametrize(
    ("current", "target", "operator", "expected"),
    [
        (790, 793, "\u2265", False),  # three tests vanished — the regression to catch
        (793, 793, "\u2265", True),
        (794, 793, "\u2265", True),  # growth is not a regression
        (88.0, 88.88, "\u2265", False),  # coverage slipped
        (1, 0, "=", False),  # a lint warning appeared
        (8, 7, "\u2264", False),  # one more vulnerability
        (6, 7, "\u2264", True),
    ],
)
def test_a_metric_moving_the_wrong_way_fails_the_gate(
    gate: QualityGate, current: float, target: float, operator: str, expected: bool
) -> None:
    """Recording a baseline is pointless unless the comparison bites.

    Every case here is a real regression the gate had to have caught while it was
    returning SKIP for want of a baseline.
    """
    assert gate._compare(current, target, operator) is expected


def test_an_unknown_operator_fails_closed(gate: QualityGate) -> None:
    """A typo in the threshold config must not silently pass everything."""
    assert gate._compare(0, 0, "~=") is False


def test_the_baseline_is_written_in_the_committed_canonical_form(gate: QualityGate, tmp_path: Path) -> None:
    """Sorted and newline-terminated, or the json-sorter hook rewrites it every run.

    The baseline is tracked, so a script that emits a different byte sequence than
    the hook's canonical form leaves a diff behind after each recording.
    """
    import json

    gate.baseline_path = tmp_path / "baseline.json"
    gate.last_report_path = tmp_path / "report.json"
    gate.config = {"commands": {"lint": "true"}, "thresholds": {}}
    gate.gates = [("Lint", "lint", "warning_count", "=", CommandSpec((("true",),)))]

    gate.baseline()

    raw = gate.baseline_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw == json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"


# ─── a tool that is not installed must not read as a clean scan ───────────────


def test_an_absent_tool_is_not_swallowed_into_a_pass(gate: QualityGate) -> None:
    """CI ran the secrets gate with no detect-secrets and reported PASS at 0.

    ``swallow_exit`` exists because these tools exit non-zero merely for finding
    something. It was also swallowing 127, so a scan that never happened yielded a
    metric of 0 and sailed under a ``<=`` threshold.
    """
    spec = CommandSpec((("definitely-not-installed-xyz",),), swallow_exit=True)

    exit_code, output = gate._run(spec)

    assert exit_code == 127
    assert "Command not found" in output


def test_a_tool_that_merely_found_something_is_still_swallowed(gate: QualityGate) -> None:
    """The behaviour swallow_exit was written for has to survive the fix."""
    spec = CommandSpec((("false",),), swallow_exit=True)  # exits 1, but exists

    exit_code, _output = gate._run(spec)

    assert exit_code == 0


def test_a_fallback_chain_still_reaches_its_second_alternative(gate: QualityGate) -> None:
    """pip-audit || npm audit: a missing first tool must not abort the chain."""
    spec = CommandSpec((("definitely-not-installed-xyz",), ("true",)), swallow_exit=True)

    exit_code, _output = gate._run(spec)

    assert exit_code == 0
