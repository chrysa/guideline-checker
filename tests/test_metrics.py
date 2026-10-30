"""Measurement primitives: they measure, they never judge."""

from guideline_checker.core.detection.numeric import METRICS, VALID_METRICS


def test_file_lines_counts_every_line() -> None:
    assert METRICS["file_lines"]("a\nb\nc\n") == [(1, 3, "file")]


def test_file_lines_on_empty_source_measures_zero() -> None:
    assert METRICS["file_lines"]("") == [(1, 0, "file")]


def test_function_lines_measures_each_function_separately() -> None:
    source = "def short():\n    return 1\n\n\ndef longer():\n    a = 1\n    b = 2\n    return a + b\n"
    measured = {subject: value for _line, value, subject in METRICS["function_lines"](source)}
    assert measured == {"short": 2, "longer": 4}


def test_function_lines_reports_the_def_line() -> None:
    [(line, _value, subject)] = METRICS["function_lines"]("x = 1\n\n\ndef here():\n    return 2\n")
    assert (line, subject) == (4, "here")


def test_function_lines_counts_an_async_function() -> None:
    [(_line, value, subject)] = METRICS["function_lines"]("async def go():\n    return 1\n")
    assert (value, subject) == (2, "go")


def test_branches_counts_decision_points_plus_one() -> None:
    source = "def f(x):\n    if x:\n        return 1\n    for _ in range(x):\n        pass\n    return 0\n"
    [(_line, value, subject)] = METRICS["branches"](source)
    assert (value, subject) == (3, "f")


def test_branches_of_a_straight_line_function_is_one() -> None:
    [(_line, value, _subject)] = METRICS["branches"]("def f():\n    return 1\n")
    assert value == 1


def test_unparseable_source_measures_nothing_rather_than_raising() -> None:
    assert METRICS["function_lines"]("def (:\n") == []
    assert METRICS["branches"]("def (:\n") == []


def test_file_lines_measures_unparseable_source_all_the_same() -> None:
    """A line count needs no parse — a broken file is still a long file."""
    assert METRICS["file_lines"]("def (:\n") == [(1, 1, "file")]


def test_valid_metrics_matches_the_registry() -> None:
    assert frozenset(METRICS) == VALID_METRICS
