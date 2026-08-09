# Numeric-threshold · JSON contract · Glossary guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining honesty gaps named by the Notion project record and the 2026-07-27 standards état-des-lieux: the `numeric-threshold` mechanism that exists in the taxonomy but measures nothing, the JSON result contract Standards Hub cannot depend on because it carries no version, and the glossary bullets the loader lifts into enforceable constraints.

**Architecture:** Three independent lots, each shipping on its own.
- **L1** adds a `detect.numeric_threshold` block to the YAML schema and a measuring detector behind it. The engine gains the *mechanism* (measure lines / functions / branches, compare to a bound); every *value* stays in the host's `guidelines/*.yml` — ADR D-0016's line, unmoved.
- **L2** stamps the JSON report with a versioned envelope (`schema_version`, rule id, kind, severity, counts) so a consumer can pin a shape instead of guessing one. Purely additive to the payload.
- **L4** teaches the markdown loader to recognise a *definition list* (a glossary of enum values) and skip it, because a value parsed as a mechanism is the exact failure D-0016 names.

**Tech Stack:** Python 3.14 (CI 3.12 + 3.14), pytest + pytest-mock, ruff, mypy strict, all invoked through `make`.

## Global Constraints

- All code, comments, docstrings, tests, commit messages and docs in **English**.
- **pytest only** — assert-style functions, `mocker` fixture. `unittest` / `unittest.mock` forbidden.
- Coverage must stay **≥ 85%**; lint warnings **0**; mypy clean.
- Max function 50 lines · max file 500 lines · cyclomatic complexity ≤ 10.
- **No hardcoded constants in engine code.** Thresholds, metric bounds and file targets come from the host's `guidelines/*.yml`. A literal bound inside `guideline_checker/` is the defect this plan exists to remove — do not reintroduce one.
- Never invoke `ruff` / `pytest` / `mypy` directly on the host: `make lint`, `make test`, `make typecheck`, `make docker-test`.
- Conventional Commits. One PR per issue, referencing it (`Closes #N`).
- The engine must never crash a scan: a file that fails to parse yields no findings, it does not raise.

---

## File Structure

| Path | Responsibility | Lot |
| --- | --- | --- |
| `guideline_checker/metrics.py` | **New.** Pure measurement functions: file line count, longest function length, max branch count. No thresholds, no I/O, no Violation. | L1 |
| `guideline_checker/loader.py` | `RuleDetector` gains `numeric_threshold`; `_extract_rules` gains the glossary guard. | L1, L4 |
| `guideline_checker/guidelines.py` | Validate the `detect.numeric_threshold` mapping. | L1 |
| `guideline_checker/kinds.py` | `kind_of_detector` returns `NUMERIC_THRESHOLD` when the block is present. | L1 |
| `guideline_checker/checker.py` | Dispatch a declarative rule carrying `numeric_threshold` to `metrics`. | L1 |
| `guidelines/languages/python.yml` | The chrysa numeric gates as host values (500 / 50 / 10). | L1 |
| `guideline_checker/reporters/json_reporter.py` | Versioned report envelope. | L2 |
| `DECISIONS.md` | ADR D-0021 (numeric-threshold mechanism) and D-0022 (JSON contract). | L1, L2 |
| `README.md` | `detect.numeric_threshold` in the rule-authoring reference; JSON contract section. | L1, L2 |
| `tests/test_metrics.py` | **New.** Measurement functions in isolation. | L1 |
| `tests/test_numeric_threshold.py` | **New.** Schema validation, kind classification, end-to-end firing. | L1 |
| `tests/test_json_contract.py` | **New.** Envelope shape and version. | L2 |
| `tests/test_loader_glossary.py` | **New.** Definition lists skipped, imperative bullets kept. | L4 |

---

## Task 1: Measurement primitives (`metrics.py`)

**Files:**
- Create: `guideline_checker/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `METRICS: dict[str, Callable[[str], list[tuple[int, int, str]]]]` — metric name → measurer.
  - `VALID_METRICS: frozenset[str]` — the metric names the YAML loader validates against.
  - Each measurer takes the file **source text** and returns `[(line_number, measured_value, subject)]`, where `subject` names what was measured (`"file"`, or a function name). It carries **no threshold**: comparison happens in the checker.
  - Metric names: `file_lines`, `function_lines`, `branches`.

- [ ] **Step 1: Write the failing test**

```python
"""Measurement primitives: they measure, they never judge."""

from guideline_checker.metrics import METRICS, VALID_METRICS


def test_file_lines_counts_every_line() -> None:
    measured = METRICS["file_lines"]("a\nb\nc\n")
    assert measured == [(1, 3, "file")]


def test_file_lines_on_empty_source_measures_zero() -> None:
    assert METRICS["file_lines"]("") == [(1, 0, "file")]


def test_function_lines_measures_each_function_separately() -> None:
    source = "def short():\n    return 1\n\n\ndef longer():\n    a = 1\n    b = 2\n    return a + b\n"
    measured = dict((subject, value) for _line, value, subject in METRICS["function_lines"](source))
    assert measured == {"short": 2, "longer": 4}


def test_function_lines_reports_the_def_line() -> None:
    source = "x = 1\n\n\ndef here():\n    return 2\n"
    [(line, _value, subject)] = METRICS["function_lines"](source)
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


def test_valid_metrics_matches_the_registry() -> None:
    assert VALID_METRICS == frozenset(METRICS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test ARGS="tests/test_metrics.py"` — or, if the Makefile target takes no args, `make test` and read the collection error.
Expected: FAIL — `ModuleNotFoundError: No module named 'guideline_checker.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Measurement primitives for the ``numeric-threshold`` mechanism (ADR D-0021).

A measurer answers *how much*, never *too much*: it returns the value it read and
the line it read it at, and knows nothing about the bound it will be compared to.
The bound is a host value from ``guidelines/*.yml``; keeping it out of this module
is what stops the engine from carrying a threshold of its own (ADR D-0016).
"""

from __future__ import annotations

import ast
from collections.abc import Callable

# What a measurer returns per subject: the line to report at, the measured value,
# and the name of what was measured ("file", or a function's name).
Measurement = tuple[int, int, str]

# Nodes that add a decision point. The count is *branches* = decision points + 1,
# the standard cyclomatic-complexity heuristic.
_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert)
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parse(source: str) -> ast.Module | None:
    """Parse ``source``, or return ``None`` — detection must never crash a scan."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def measure_file_lines(source: str) -> list[Measurement]:
    """The file's line count, reported at line 1."""
    return [(1, len(source.splitlines()), "file")]


def measure_function_lines(source: str) -> list[Measurement]:
    """Each function's span in lines, reported at its ``def`` line."""
    tree = _parse(source)
    if tree is None:
        return []
    return [
        (node.lineno, (node.end_lineno or node.lineno) - node.lineno + 1, node.name)
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTION_NODES)
    ]


def _branch_count(node: ast.AST) -> int:
    """Decision points in a function body, plus one for the entry path."""
    return 1 + sum(1 for inner in ast.walk(node) if isinstance(inner, _BRANCH_NODES))


def measure_branches(source: str) -> list[Measurement]:
    """Each function's branch count, reported at its ``def`` line."""
    tree = _parse(source)
    if tree is None:
        return []
    return [
        (node.lineno, _branch_count(node), node.name)
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTION_NODES)
    ]


METRICS: dict[str, Callable[[str], list[Measurement]]] = {
    "file_lines": measure_file_lines,
    "function_lines": measure_function_lines,
    "branches": measure_branches,
}

# Exposed for the YAML loader to validate ``detect.numeric_threshold.metric`` against.
VALID_METRICS: frozenset[str] = frozenset(METRICS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test`
Expected: PASS, all new tests green, no existing test broken.

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): measurement primitives for the numeric-threshold kind"
```

---

## Task 2: `detect.numeric_threshold` in the YAML schema

**Files:**
- Modify: `guideline_checker/loader.py` (the `RuleDetector` dataclass)
- Modify: `guideline_checker/guidelines.py:60-90` (key constants), `:590-680` (`_build_detector`, new validator)
- Modify: `guideline_checker/kinds.py:75-95` (`kind_of_detector`)
- Test: `tests/test_numeric_threshold.py`

**Interfaces:**
- Consumes: `VALID_METRICS` from Task 1.
- Produces:
  - `loader.NumericThreshold` — frozen dataclass, fields `metric: str`, `max_value: int`.
  - `RuleDetector.numeric_threshold: NumericThreshold | None` (default `None`).
  - YAML shape: `detect: {numeric_threshold: {metric: file_lines, max: 500}}`.
  - `kind_of_detector` returns `CheckKind.NUMERIC_THRESHOLD` when the block is set, ranked **above** the pattern kinds and **below** freshness/cross-reference (it is a measurement, not a pattern match).

- [ ] **Step 1: Write the failing test**

```python
"""``detect.numeric_threshold``: the host names the metric and the bound."""

import pytest

from guideline_checker.guidelines import GuidelineError, load_guidelines
from guideline_checker.kinds import CheckKind, kind_of_detector
from guideline_checker.loader import NumericThreshold, RuleDetector

_CATEGORIES = """
categories:
  - id: correctness
    description: "Correctness"
"""


def _write(tmp_path, body: str):
    (tmp_path / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    languages = tmp_path / "languages"
    languages.mkdir(exist_ok=True)
    (languages / "python.yml").write_text(body, encoding="utf-8")
    return tmp_path


def test_a_valid_block_loads_into_a_numeric_threshold(tmp_path) -> None:
    root = _write(
        tmp_path,
        """
target: "**/*.py"
rules:
  - id: py-file-too-long
    category: correctness
    severity: warning
    rule: "A source file stays under the fleet file-length bound"
    detect:
      numeric_threshold:
        metric: file_lines
        max: 500
""",
    )
    [rule] = load_guidelines(root)
    assert rule.detect is not None
    assert rule.detect.numeric_threshold == NumericThreshold(metric="file_lines", max_value=500)


def test_an_unknown_metric_is_a_hard_failure(tmp_path) -> None:
    root = _write(
        tmp_path,
        """
target: "**/*.py"
rules:
  - id: py-bad-metric
    category: correctness
    severity: warning
    rule: "Unknown metric"
    detect:
      numeric_threshold:
        metric: vibes
        max: 3
""",
    )
    with pytest.raises(GuidelineError, match="unknown metric"):
        load_guidelines(root)


def test_a_missing_max_is_a_hard_failure(tmp_path) -> None:
    root = _write(
        tmp_path,
        """
target: "**/*.py"
rules:
  - id: py-no-max
    category: correctness
    severity: warning
    rule: "No bound"
    detect:
      numeric_threshold:
        metric: file_lines
""",
    )
    with pytest.raises(GuidelineError, match="'max'"):
        load_guidelines(root)


def test_a_non_positive_max_is_a_hard_failure(tmp_path) -> None:
    root = _write(
        tmp_path,
        """
target: "**/*.py"
rules:
  - id: py-zero-max
    category: correctness
    severity: warning
    rule: "Zero bound"
    detect:
      numeric_threshold:
        metric: file_lines
        max: 0
""",
    )
    with pytest.raises(GuidelineError, match="positive integer"):
        load_guidelines(root)


def test_the_block_alone_satisfies_the_non_empty_detector_check(tmp_path) -> None:
    root = _write(
        tmp_path,
        """
target: "**/*.py"
rules:
  - id: py-only-threshold
    category: correctness
    severity: warning
    rule: "A function stays under the fleet length bound"
    detect:
      numeric_threshold:
        metric: function_lines
        max: 50
""",
    )
    assert load_guidelines(root)


def test_a_detector_carrying_a_threshold_classifies_as_numeric_threshold() -> None:
    detector = RuleDetector(numeric_threshold=NumericThreshold(metric="branches", max_value=10))
    assert kind_of_detector(detector) is CheckKind.NUMERIC_THRESHOLD


def test_a_threshold_outranks_a_pattern_on_the_same_rule() -> None:
    detector = RuleDetector(
        forbid=("print(",),
        numeric_threshold=NumericThreshold(metric="file_lines", max_value=500),
    )
    assert kind_of_detector(detector) is CheckKind.NUMERIC_THRESHOLD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test`
Expected: FAIL — `ImportError: cannot import name 'NumericThreshold' from 'guideline_checker.loader'`

> If `RuleDetector` is not constructible with keyword-only defaults in this codebase, read `guideline_checker/loader.py` and match its existing construction style rather than changing it.

- [ ] **Step 3: Write minimal implementation**

In `guideline_checker/loader.py`, beside the existing `CrossReference` dataclass:

```python
@dataclass(frozen=True)
class NumericThreshold:
    """A metric to measure and the bound it must not cross.

    The engine owns the *measuring* (see :mod:`guideline_checker.metrics`); this
    carries the host's chosen metric name and bound, and nothing else.
    """

    metric: str
    max_value: int
```

and add to `RuleDetector`:

```python
    numeric_threshold: NumericThreshold | None = None
```

In `guideline_checker/guidelines.py`, beside `_DETECT_FRESHNESS_KEY`:

```python
# A measured metric compared to a host-supplied bound (numeric-threshold kind, ADR D-0021).
_DETECT_NUMERIC_KEY = "numeric_threshold"
_NUMERIC_FIELDS = ("metric", "max")
```

Add the validator:

```python
def _build_numeric_threshold(path: Path, rule_id: object, value: object) -> NumericThreshold | None:
    """Validate ``detect.numeric_threshold`` — a known metric and a positive bound.

    Both fields are required together: a metric with no bound measures without
    judging, and a bound with no metric judges nothing.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GuidelineError(f"{path}: rule {rule_id!r} 'detect.{_DETECT_NUMERIC_KEY}' must be a mapping.")
    missing = [field for field in _NUMERIC_FIELDS if field not in value]
    if missing:
        raise GuidelineError(
            f"{path}: rule {rule_id!r} 'detect.{_DETECT_NUMERIC_KEY}' is missing {missing} "
            f"(both {list(_NUMERIC_FIELDS)} are required).",
        )
    metric = value["metric"]
    if not isinstance(metric, str) or metric not in VALID_METRICS:
        raise GuidelineError(
            f"{path}: rule {rule_id!r} 'detect.{_DETECT_NUMERIC_KEY}' has unknown metric {metric!r} "
            f"(available: {sorted(VALID_METRICS)}).",
        )
    bound = value["max"]
    if isinstance(bound, bool) or not isinstance(bound, int) or bound <= 0:
        raise GuidelineError(
            f"{path}: rule {rule_id!r} 'detect.{_DETECT_NUMERIC_KEY}.max' must be a positive integer.",
        )
    return NumericThreshold(metric=metric, max_value=bound)
```

Wire it into `_build_detector`: add `_DETECT_NUMERIC_KEY` to `allowed`, compute
`numeric_threshold = _build_numeric_threshold(path, raw["id"], block.get(_DETECT_NUMERIC_KEY))`,
add `or numeric_threshold is not None` to `has_any`, add `_DETECT_NUMERIC_KEY` to the
`detect_keys` list in the empty-block error, and pass `numeric_threshold=numeric_threshold`
to the `RuleDetector(...)` construction. Import `NumericThreshold` from `loader` and
`VALID_METRICS` from `metrics` at the top of the module.

In `guideline_checker/kinds.py`, inside `kind_of_detector`, after the cross-reference branch
and before the AST branch:

```python
    if detector.numeric_threshold is not None:
        return CheckKind.NUMERIC_THRESHOLD
```

Also extend `_merge_detectors` in `guidelines.py` so an inheriting rule keeps a base's
threshold when it declares none of its own (`child.numeric_threshold or base.numeric_threshold`),
matching how the other fields merge.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test && make typecheck && make lint`
Expected: PASS on all three.

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/loader.py guideline_checker/guidelines.py guideline_checker/kinds.py tests/test_numeric_threshold.py
git commit -m "feat(guidelines): detect.numeric_threshold — a metric and a host-supplied bound"
```

---

## Task 3: The checker measures and fires

**Files:**
- Modify: `guideline_checker/checker.py` (the declarative-detector dispatch)
- Test: `tests/test_numeric_threshold.py` (append)

**Interfaces:**
- Consumes: `METRICS` (Task 1), `RuleDetector.numeric_threshold` (Task 2).
- Produces: a declarative rule carrying `numeric_threshold` yields one `Violation` per subject over the bound, at the subject's line, with `line_content` naming the measurement (`"file has 612 lines (max: 500)"` / `"function 'build' has 71 lines (max: 50)"`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_numeric_threshold.py`:

```python
from guideline_checker.checker import GuidelineChecker


def _project(tmp_path, rules_body: str, source: str):
    guidelines = tmp_path / "guidelines"
    (guidelines / "languages").mkdir(parents=True)
    (guidelines / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (guidelines / "languages" / "python.yml").write_text(rules_body, encoding="utf-8")
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    return tmp_path


_FILE_LINES_RULE = """
target: "**/*.py"
rules:
  - id: py-file-too-long
    category: correctness
    severity: warning
    rule: "A source file stays under the fleet file-length bound"
    detect:
      numeric_threshold:
        metric: file_lines
        max: 3
"""


def test_a_file_over_the_bound_fires(tmp_path) -> None:
    root = _project(tmp_path, _FILE_LINES_RULE, "a = 1\nb = 2\nc = 3\nd = 4\n")
    violations = [v for result in GuidelineChecker(root).check() for v in result.violations]
    assert [v.line_number for v in violations] == [1]
    assert "4" in violations[0].line_content


def test_a_file_at_the_bound_does_not_fire(tmp_path) -> None:
    root = _project(tmp_path, _FILE_LINES_RULE, "a = 1\nb = 2\nc = 3\n")
    assert [v for result in GuidelineChecker(root).check() for v in result.violations] == []


def test_a_long_function_fires_at_its_def_line(tmp_path) -> None:
    rules = """
target: "**/*.py"
rules:
  - id: py-function-too-long
    category: correctness
    severity: warning
    rule: "A function stays under the fleet function-length bound"
    detect:
      numeric_threshold:
        metric: function_lines
        max: 2
"""
    root = _project(tmp_path, rules, "x = 0\n\n\ndef big():\n    a = 1\n    b = 2\n    return a + b\n")
    [violation] = [v for result in GuidelineChecker(root).check() for v in result.violations]
    assert violation.line_number == 4
    assert "big" in violation.line_content
```

> Read `guideline_checker/checker.py` for the exact `GuidelineChecker` construction and
> `check()` return shape before writing these; match the signature the existing suite uses
> (`tests/test_guidelines.py` is the reference) rather than the sketch above.

- [ ] **Step 2: Run test to verify it fails**

Run: `make test`
Expected: FAIL — no violations produced; the detector loads but nothing measures.

- [ ] **Step 3: Write minimal implementation**

In `checker.py`, in the function that runs a `RuleDetector` against one file's content
(the same place `scan_checks` and `ast_checks` are dispatched), add:

```python
def _numeric_threshold_violations(
    file_path: Path,
    content: str,
    threshold: NumericThreshold,
    rule: str,
    severity: str,
) -> list[Violation]:
    """Measure the rule's metric and flag every subject over the host's bound."""
    measurer = METRICS[threshold.metric]
    return [
        Violation(
            file=file_path,
            line_number=line,
            line_content=_measurement_text(subject, value, threshold.max_value),
            rule=rule,
            severity=severity,
        )
        for line, value, subject in measurer(content)
        if value > threshold.max_value
    ]


def _measurement_text(subject: str, value: int, bound: int) -> str:
    """Human-readable evidence: what was measured, how much, against which bound."""
    what = "file" if subject == "file" else f"function {subject!r}"
    return f"{what} measured {value} (max: {bound})"
```

and call it from the dispatch when `detector.numeric_threshold is not None`, extending the
violation list the same way the scanner and AST branches do. Import `METRICS` from
`guideline_checker.metrics` and `NumericThreshold` from `guideline_checker.loader`.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test && make typecheck && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/checker.py tests/test_numeric_threshold.py
git commit -m "feat(checker): fire the numeric-threshold mechanism against measured metrics"
```

---

## Task 4: The chrysa numeric gates as host values, ADR + README

**Files:**
- Modify: `guidelines/languages/python.yml`
- Modify: `DECISIONS.md`
- Modify: `README.md`
- Test: `tests/test_numeric_threshold.py` (append a referential guard)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: three shipped rules — `py-file-length`, `py-function-length`, `py-branch-count` — and the guarantee that `NUMERIC_THRESHOLD` is no longer a kind with zero rules.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_numeric_threshold.py`:

```python
from pathlib import Path

from guideline_checker.kinds import kind_of_detector

_REPO_GUIDELINES = Path(__file__).resolve().parent.parent / "guidelines"


def test_the_shipped_referential_arms_the_numeric_threshold_kind() -> None:
    """A kind in the taxonomy with no rule behind it is a mechanism that measures nothing."""
    rules = load_guidelines(_REPO_GUIDELINES)
    armed = [r for r in rules if kind_of_detector(r.detect) is CheckKind.NUMERIC_THRESHOLD]
    assert {r.id for r in armed} >= {"py-file-length", "py-function-length", "py-branch-count"}


def test_no_shipped_threshold_bound_is_duplicated_in_engine_code() -> None:
    """The bounds live in the host referential; the engine must not restate them."""
    engine = (Path(__file__).resolve().parent.parent / "guideline_checker" / "metrics.py").read_text(encoding="utf-8")
    assert "500" not in engine
    assert "max_value" not in engine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test`
Expected: FAIL — the referential has no rule of that kind yet.

- [ ] **Step 3: Write minimal implementation**

Append to `guidelines/languages/python.yml` (under its existing `rules:` list, matching the
file's indentation and field order — read the neighbouring rules first):

```yaml
  - id: py-file-length
    category: correctness
    severity: warning
    rule: "A source file stays under the fleet file-length bound"
    rationale: >
      The fleet quality gate caps a file at 500 lines. Encoding it here means the
      bound is a host value the referential owns, not a literal buried in the engine.
    detect:
      numeric_threshold:
        metric: file_lines
        max: 500

  - id: py-function-length
    category: correctness
    severity: warning
    rule: "A function stays under the fleet function-length bound"
    rationale: >
      A function past 50 lines does more than one thing at one level of abstraction,
      which is what makes it untestable in isolation.
    detect:
      numeric_threshold:
        metric: function_lines
        max: 50

  - id: py-branch-count
    category: correctness
    severity: warning
    rule: "A function stays under the fleet branch-count bound"
    rationale: >
      Branch count is the cyclomatic-complexity heuristic the fleet gate caps at 10.
      A dispatch table keeps it flat where an if/elif ladder would not.
    detect:
      numeric_threshold:
        metric: branches
        max: 10
```

Add ADR **D-0021** to `DECISIONS.md`, following the shape of D-0020 and carrying the three
falsifiable fields the chrysa ADR format requires:

- **Context:** `NUMERIC_THRESHOLD` shipped in the D-0020 taxonomy with no detector and no
  YAML key. A kind that cannot be authored is a mechanism that measures nothing — the
  silent green this tool exists to refuse, inside its own taxonomy.
- **Decision:** the engine owns three measurers (`file_lines`, `function_lines`, `branches`)
  and a `detect.numeric_threshold: {metric, max}` block. The metric name and the bound are
  **host values**; no bound exists in engine code.
- **Fatal hypothesis:** measuring length and branch count from a single-file AST is close
  enough to what the fleet gate (`ruff` `C901` / `PLR0915`) measures that a rule firing here
  predicts a CI failure there.
- **Kill-test:** run both over the fleet's ten largest repos; if the two disagree on more
  than 10% of functions, the mechanism is measuring something else and must defer to the
  linter instead of restating it. Checked at the next referential review.
- **Validation gate:** the shipped rules fire on real code in at least one repo before any
  promotion from `warning` to `error`.
- **Consequences:** severity stays `warning` — the fleet gate already blocks on `ruff`, and
  a second blocking source for the same bound would double-report.

Add a `detect.numeric_threshold` entry to the README's rule-authoring reference, in the same
table/section as `stale_after_days` and `cross_reference`, with the YAML snippet above and
the metric list.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test && make lint && make typecheck`
Expected: PASS. Then run the tool on itself and read the output:

Run: `python -m guideline_checker.cli check --root . --fail-on error`
Expected: exit 0 (the new rules are `warning`), and the new rules visible in the report.
If they fire on this repo's own files, that is real debt surfaced, not a bug — record the
count in the PR body.

- [ ] **Step 5: Commit**

```bash
git add guidelines/languages/python.yml DECISIONS.md README.md tests/test_numeric_threshold.py
git commit -m "feat(guidelines): ship the fleet numeric gates as host values (ADR D-0021)"
```

---

## Task 5: Versioned JSON result contract

**Files:**
- Modify: `guideline_checker/reporters/json_reporter.py`
- Modify: `DECISIONS.md` (ADR D-0022)
- Modify: `README.md`
- Test: `tests/test_json_contract.py`

**Interfaces:**
- Consumes: `RuleResult` / `Violation` as they already exist.
- Produces: a report envelope with `schema_version: "1.0"` at the top level, and each
  violation carrying `rule_id` and `kind` alongside the fields it already has. Every
  existing field is **kept** — this is additive, so no current consumer breaks.

- [ ] **Step 1: Write the failing test**

```python
"""The JSON report is a contract: a consumer pins a version, not a guess."""

import json
from pathlib import Path

from guideline_checker.reporters.json_reporter import SCHEMA_VERSION, JsonReporter


def _report(tmp_path: Path, results) -> dict:
    out = tmp_path / "report.json"
    JsonReporter().write(results, out, tmp_path)
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_envelope_declares_a_schema_version(tmp_path) -> None:
    assert _report(tmp_path, [])["schema_version"] == SCHEMA_VERSION


def test_the_schema_version_is_a_major_minor_string() -> None:
    major, _, minor = SCHEMA_VERSION.partition(".")
    assert major.isdigit() and minor.isdigit()


def test_the_envelope_keeps_the_fields_existing_consumers_read(tmp_path) -> None:
    report = _report(tmp_path, [])
    assert {"generated_at", "project_root", "summary", "rules"} <= set(report)


def test_the_summary_counts_every_severity_even_at_zero(tmp_path) -> None:
    summary = _report(tmp_path, [])["summary"]
    assert summary == {"files_checked": 0, "total_violations": 0, "errors": 0, "warnings": 0, "info": 0}
```

Then add a test that builds one real `RuleResult` with one `Violation` — read
`tests/test_json_reporter.py` (or whichever existing test constructs these) and reuse its
fixture rather than inventing a second construction path — and assert:

```python
def test_a_violation_carries_its_rule_id_and_kind(tmp_path, one_result_with_violation) -> None:
    [rule] = _report(tmp_path, [one_result_with_violation])["rules"]
    [violation] = rule["violations"]
    assert set(violation) >= {"severity", "file", "line", "content", "rule", "rule_id", "kind"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test`
Expected: FAIL — `ImportError: cannot import name 'SCHEMA_VERSION'`

- [ ] **Step 3: Write minimal implementation**

In `json_reporter.py`:

```python
# The result contract's own version, independent of the tool's release version.
# Bump the minor for an additive field, the major for a removal or a changed meaning.
# Standards Hub and any other consumer pin this, not the tool's git tag (ADR D-0022).
SCHEMA_VERSION = "1.0"
```

Add `"schema_version": SCHEMA_VERSION` as the **first** key of the `report` dict, and add
`"rule_id"` and `"kind"` to each violation entry, read from the violation's originating
rule. If `Violation` does not carry its rule id today, source them from the enclosing
`RuleResult` — read `checker.RuleResult` first and take whichever is already available
rather than threading a new field through the checker. When a value is genuinely absent
(a phrase-derived rule has no id), emit `""` rather than omitting the key: a contract with
optional keys is a contract a consumer cannot pin.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test && make typecheck && make lint`
Expected: PASS.

- [ ] **Step 5: Document and commit**

Add ADR **D-0022** to `DECISIONS.md`:

- **Context:** Standards Hub is to consume compliance results without touching the engine's
  internals, but the JSON report carried no version — a consumer could only pin the tool's
  git tag, coupling itself to every unrelated release.
- **Decision:** the report carries `schema_version`, versioned independently of the tool.
  Additive change bumps the minor; a removal or a changed field meaning bumps the major.
  SARIF keeps its own `2.1.0` — that version belongs to the SARIF spec, not to us.
- **Fatal hypothesis:** a consumer can be served by a stable JSON shape without ever
  reaching into the engine.
- **Kill-test:** if Standards Hub's first integration needs a field the contract does not
  carry, the contract was designed from the producer's side; re-derive it from the
  consumer's query, in one dated revision. Checked at Hub integration.
- **Validation gate:** Standards Hub reads a report end-to-end using only documented fields.
- **Consequences:** the Hub never blocks local or CI execution — a report is a file on disk.

Add a "JSON result contract" section to `README.md` documenting the envelope, the version
policy, and one full example payload.

```bash
git add guideline_checker/reporters/json_reporter.py tests/test_json_contract.py DECISIONS.md README.md
git commit -m "feat(reporters): versioned JSON result contract for Standards Hub (ADR D-0022)"
```

---

## Task 6: Glossary bullets are values, not constraints (#255)

**Files:**
- Modify: `guideline_checker/loader.py:289-311` (`_extract_rules`)
- Test: `tests/test_loader_glossary.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_extract_rules` skips a bullet that is a **definition entry** — a short
  code-or-bold term followed by a dash separator — when the same bullet list holds **two or
  more** such entries. One lone dash-bullet stays a rule; a five-entry enum glossary does not
  become five constraints.

- [ ] **Step 1: Write the failing test**

```python
"""A glossary defines values. A value parsed as a mechanism is ADR D-0016's failure."""

from guideline_checker.loader import _extract_rules

_RUNTIME_GLOSSARY = """
## Container-runtime policy

Every repo carries a `runtime:` field in `repos.yml`:

- `container` — runs as a service. Provides Dockerfile(s) + docker-compose + HEALTHCHECK.
- `exempt:lib` — distributed or imported (library, plugin, pre-commit hook, CLI).
- `exempt:config` — no executable runtime (config, knowledge base, deploy manifests). Nothing to run.
- `exempt:native` — bound to a host OS, device, cloud platform, or editor.
- `pending` — pre-code scaffold; flips to `container` at first code.
"""


def test_an_enum_glossary_yields_no_rules() -> None:
    assert _extract_rules(_RUNTIME_GLOSSARY) == []


def test_prose_around_a_glossary_is_still_read() -> None:
    content = _RUNTIME_GLOSSARY + "\n- Never commit a secret to the repository\n"
    assert any("Never commit a secret" in rule for rule in _extract_rules(content))


def test_a_single_dash_bullet_is_still_a_rule() -> None:
    content = "- `print()` — never call it in production code\n"
    assert _extract_rules(content) == ["print() — never call it in production code"]


def test_a_colon_bullet_is_a_rule_not_a_definition() -> None:
    content = (
        "- **Language**: English — all code, comments and docs.\n"
        "- **Commits**: Conventional Commits, always.\n"
    )
    assert len(_extract_rules(content)) == 2


def test_two_separate_lists_are_judged_separately() -> None:
    content = _RUNTIME_GLOSSARY + "\n## Rules\n\n- Always pin a dependency version\n- Never use a bare except\n"
    rules = _extract_rules(content)
    assert len(rules) == 2
    assert all("exempt" not in rule for rule in rules)


def test_a_long_sentence_starting_with_code_is_not_a_definition() -> None:
    content = (
        "- `venv/` — forbidden inside a project tree, the interpreter runs in the image instead.\n"
        "- `node_modules/` — forbidden inside a project tree, deps live in a named volume instead.\n"
    )
    # Both carry an imperative ("forbidden"): constraint keywords override the shape heuristic.
    assert len(_extract_rules(content)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test`
Expected: FAIL — `test_an_enum_glossary_yields_no_rules` gets 5 rules.

- [ ] **Step 3: Write minimal implementation**

In `loader.py`, beside the existing regexes:

```python
# A definition entry: a short code-or-bold term, then a dash, then its meaning
# ("- `exempt:config` — no executable runtime"). Two or more in one list is a
# glossary: the bullets define the allowed *values* of a field, they do not
# impose anything. ADR D-0016 — a value parsed as a mechanism is the failure mode
# this guard exists for.
_DEFINITION_RE = re.compile(r"^(?:`[^`]{1,40}`|\*\*[^*]{1,40}\*\*)\s*[—–-]\s+\S")
# Below this, a run of definition bullets reads as an ordinary rule list.
_GLOSSARY_MIN_ENTRIES = 2
```

Then restructure `_extract_rules` to walk **bullet blocks** rather than bare lines: a block
is a maximal run of consecutive list items (blank lines and non-list lines end it). For each
block, drop it entirely when it holds `>= _GLOSSARY_MIN_ENTRIES` definition entries **and**
none of its entries carries a constraint keyword; otherwise keep its rules as today.

```python
def _is_definition_entry(rule_text: str) -> bool:
    """True for a bullet that defines a term rather than imposing anything."""
    return bool(_DEFINITION_RE.match(rule_text))


def _is_glossary(block: list[str]) -> bool:
    """True for a run of bullets that defines the values of a field.

    An imperative anywhere in the block disqualifies it: a list that both defines
    and demands is still read, because dropping it would lose a real constraint.
    """
    if len(block) < _GLOSSARY_MIN_ENTRIES:
        return False
    if any(kw in text.lower() for text in block for kw in _CONSTRAINT_KEYWORDS):
        return False
    return sum(1 for text in block if _is_definition_entry(text)) >= _GLOSSARY_MIN_ENTRIES
```

Keep the existing `len(clean) > 10` and dedupe behaviour unchanged. Keep `_extract_rules`
under 50 lines: extract the block-walking into a `_bullet_blocks(content)` helper that
yields `list[str]` of raw rule texts.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test && make lint && make typecheck`
Expected: PASS, and the whole existing suite still green — this changes extraction for every
markdown source, so a regression shows up in `tests/test_loader.py` first.

Then measure the real effect:

Run: `python -m guideline_checker.cli check --root . --json /tmp/before-after.json`
Expected: fewer advisory rules than the 571 counted before this task, with no loss of a rule
that carries an imperative. Record the delta in the PR body.

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/loader.py tests/test_loader_glossary.py
git commit -m "fix(loader): a definition list defines values, it does not impose them

Closes #255"
```

---

## Task 7: Close the stale issue and verify the gate

**Files:** none — verification only.

- [ ] **Step 1: Confirm #320 is already fixed on `main`**

Run: `git log --oneline -1 -- guideline_checker/ast_python.py && make test`
Expected: `bd68a50 fix(ast): sync-fastapi-route must read the body, not just the def keyword (#324)`
and `tests/test_sync_route_body.py` green. If both hold, the issue is stale.

- [ ] **Step 2: Close it with the evidence**

```bash
gh issue close 320 --repo chrysa/guideline-checker \
  --comment "Fixed on main by #324 (bd68a50): \`_check_sync_fastapi_route\` now reads the body via \`_body_blocks\` and skips a handler that performs blocking I/O. Covered by tests/test_sync_route_body.py. The known limit — blocking work one call away stays invisible to a single-file AST pass — is documented in the docstring, and the rule remains a warning for that reason."
```

- [ ] **Step 3: Run the full local gate**

Run: `make ci`
Expected: lint + format-check + typecheck + docker-test all green, coverage ≥ 85%.

- [ ] **Step 4: Re-scan and compare against the pre-change baseline**

Run: `python -m guideline_checker.cli check --root . --json after.json` and compare the
rule-health summary to the session's starting point (`3 proven · 36 armed · 0 dead ·
571 advisory`, grade B, 0 errors, 19 warnings). Expected: `dead` stays 0, `advisory` drops
(Task 6), and the numeric-threshold rules appear as `proven` or `armed` (Task 4).

- [ ] **Step 5: Record the outcome in Notion**

Append a dated, append-only log entry to the `guideline-checker` project page
(`37f59293-e35e-817e-a832-ef9b05b8b042`) covering: the numeric-threshold mechanism armed
(D-0021), the versioned JSON contract (D-0022), #255 closed, #320 closed as already fixed,
and the one item still open — **#310**, which lives in `chrysa/github-actions` and cannot be
fixed from this repo.

---

## Self-Review

**Spec coverage.** L1 → Tasks 1–4. L2 → Task 5. L4 → Task 6. L5 → Task 7 (already fixed
upstream; verified and closed, not reimplemented). L3 (#310) is **out of scope by
construction** — the job comes from `chrysa/github-actions/.github/workflows/quality-gate-check.yml`
and no change in this repo can install those tools. Task 7 Step 5 records it as the one item
left open.

**Placeholders.** None: every code step carries the code, every test step carries the
assertions. Three steps deliberately say *read the existing file first* (Task 3's
`GuidelineChecker` construction, Task 5's violation fixture, Task 6's regression surface)
because the exact local signature must be matched rather than guessed — each names the file
to read and the existing test to copy from.

**Type consistency.** `NumericThreshold(metric, max_value)` is defined in Task 2 and used
under that exact name in Tasks 3 and 4. `METRICS` / `VALID_METRICS` are defined in Task 1 and
consumed in Tasks 2 and 3. The YAML key is `numeric_threshold` with fields `metric` / `max`
throughout; the Python attribute is `max_value` because `max` shadows a builtin — the
validator is the single place that translates between the two.
