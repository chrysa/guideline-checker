# guideline-checker v2 Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `guideline_checker` into `core/` (lint engine, zero LLM/gh deps), `workshop/`
(LLM detector authoring, `[workshop]` extra) and `fleet/` (gh-backed multi-repo governance,
`[fleet]` extra), replace the hardcoded phrase table with a heuristic seed-translator that
feeds a proven, locally-cached detector set, and make rule-health the headline of every report.

**Architecture:** Mechanical decomposition of `checker.py` (1265 LOC), `guidelines.py`,
`rule_health.py` and `sandbox.py` into `core/detection/*`, plus one genuinely new mechanism
(`core/derive/` — heuristic prose→detector derivation with a local ephemeral cache). Existing
`proposer.py`/`persist.py`/`interpret.py` relocate to `workshop/` unchanged; existing
`gh_client.py`/`distribution.py`/`manifest.py`/`origin_audit.py`/`lifecycle.py` relocate to
`fleet/` unchanged. `web/central.py` and the `push`/`central` CLI commands are deleted.

**Tech Stack:** Python ≥3.12, PyYAML, tree-sitter (JS/TS), pytest + pytest-cov + pytest-mock,
ruff, mypy. No new third-party dependency is introduced by this plan.

**Spec:** `docs/superpowers/specs/2026-08-19-guideline-checker-redesign-design.md` — this plan
implements it in full; executors should read both.

## Global Constraints

- `core/` **never** imports `guideline_checker.workshop` or `guideline_checker.fleet` — enforced
  by an automated boundary test from Task 1 onward (spec §6 "Boundary test").
- No new third-party dependency: `proposer.py` and `sandbox.py` already use only stdlib
  (`json`, `os`, `re`, `subprocess`, `urllib`, `pathlib`, `dataclasses`) — confirmed by reading
  their imports. `[workshop]` and `[fleet]` extras exist for **future** dependency gating and
  import-boundary documentation, not because a dependency exists today.
- **Locked resolution of spec §8 open questions** (decided now, per "pick during the first
  implementation lot"):
  1. **Plugin mechanism:** a lazy `try/except ImportError`-guarded import at the call site
     (`cli.py`, `web/app.py`) — not `importlib.metadata` entry points. `core`, `workshop` and
     `fleet` ship in the same wheel; extras only gate third-party deps, so dynamic plugin
     discovery is unneeded complexity for this repo's size.
  2. **Sandbox ownership:** `sandbox.py`'s two proof functions (`_collect_files`,
     `_declared_violations`, `_matches_pattern`-consuming logic) have zero LLM/gh imports today
     — they are the "core sandbox path, read-only" spec §4 already names. They fold into
     `core/health.py` (spec 3.5: "`health.py` gains a second job: it is the gate"). `workshop/`
     keeps no separate `sandbox.py`; `proposer.py` imports the proof function from
     `guideline_checker.core.health` (satellite → core, the legal direction).
  3. **Cache location:** `.guideline-cache/` at repo root, override via `GUIDELINE_CACHE_DIR`
     env var — matches the existing env-driven config convention (`CENTRAL_STORE` in
     `web/central.py`). No CLI flag in v1.
  4. **Proof richness:** keep the current heuristic proof as-is (no shipped fixtures per rule)
     — spec allows this ("current heuristic proof suffices" is an accepted resolution).
- Every relocation task ends with the **full existing test suite green** — relocations must not
  change behavior; only Tasks 3, 4, 6, 7, 8's plugin-loader step, and 10 change behavior and get
  fresh failing tests first.
- `docker-test` remains the authoritative CI gate (spec §6); local `pytest` is the fast loop.

---

### Task 1: Package skeleton + import-boundary guard + extras scaffold

**Files:**
- Create: `guideline_checker/core/__init__.py`, `guideline_checker/core/detection/__init__.py`,
  `guideline_checker/core/derive/__init__.py`, `guideline_checker/workshop/__init__.py`,
  `guideline_checker/fleet/__init__.py`
- Create: `tests/test_core_boundary.py`
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)

**Interfaces:**
- Produces: an importable, empty `guideline_checker.core`, `guideline_checker.core.detection`,
  `guideline_checker.core.derive`, `guideline_checker.workshop`, `guideline_checker.fleet`
  package tree that later tasks populate. Produces the boundary test every later task must keep
  green.

- [ ] **Step 1: Create the five empty package `__init__.py` files**

```bash
mkdir -p guideline_checker/core/detection guideline_checker/core/derive \
         guideline_checker/workshop guideline_checker/fleet
: > guideline_checker/core/__init__.py
: > guideline_checker/core/detection/__init__.py
: > guideline_checker/core/derive/__init__.py
: > guideline_checker/workshop/__init__.py
: > guideline_checker/fleet/__init__.py
```

- [ ] **Step 2: Write the boundary guard test**

```python
"""Import-boundary conformance (spec: docs/superpowers/specs/2026-08-19-guideline-checker-redesign-design.md, §6).

core/ must never import workshop/ or fleet/ — satellites depend on core, never the reverse.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "guideline_checker"
_FORBIDDEN_PREFIXES = ("guideline_checker.workshop", "guideline_checker.fleet")


def _core_python_files() -> list[Path]:
    core_dir = _PACKAGE_ROOT / "core"
    if not core_dir.exists():
        return []
    return sorted(core_dir.rglob("*.py"))


def _imported_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("core_file", _core_python_files(), ids=lambda p: str(p.relative_to(_PACKAGE_ROOT)))
def test_core_module_does_not_import_workshop_or_fleet(core_file: Path) -> None:
    imported = _imported_modules(core_file.read_text(encoding="utf-8"))
    violations = [m for m in imported if m.startswith(_FORBIDDEN_PREFIXES)]
    assert not violations, f"{core_file} imports satellite module(s): {violations}"


def test_boundary_test_covers_at_least_one_core_file_once_core_is_populated() -> None:
    # Guards against a silent no-op: once Task 2 lands, this must find real files.
    # Skipped until core/ has content — flip to a hard assertion after Task 2.
    pass
```

- [ ] **Step 3: Run the boundary test**

Run: `pytest tests/test_core_boundary.py -v`
Expected: PASS (0 files collected in the parametrize — vacuously true; `core/` is still empty).

- [ ] **Step 4: Add the extras scaffold to `pyproject.toml`**

In `[project.optional-dependencies]`, alongside the existing `web`, `dev`, `e2e` keys, add:

```toml
workshop = []  # LLM detector authoring (proposer/persist/interpret) — no extra deps today;
               # reserved for a future LLM SDK so it never lands in the default install.
fleet = []     # gh-backed multi-repo governance — no extra deps today (gh_client shells to
               # the `gh` CLI); reserved so a future PyGithub dependency never lands by default.
```

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -q`
Expected: PASS, same pass count as before this task (no behavior touched).

```bash
git add guideline_checker/core guideline_checker/workshop guideline_checker/fleet \
        tests/test_core_boundary.py pyproject.toml
git commit -m "feat(core): scaffold core/workshop/fleet packages + import-boundary guard"
```

---

### Task 2: Decompose `checker.py` into `core/detection/`

**Files:**
- Create: `guideline_checker/core/detection/pattern.py`, `.../numeric.py`, `.../presence.py`,
  `.../crossref.py`
- Modify (move into `core/detection/`, keep filenames): `ast_python.py`, `ast_javascript.py`,
  `scanners.py`, `scanner_source.py`
- Modify: `guideline_checker/core/detection/__init__.py` (the new orchestrator, absorbs
  `run_checks`, `_check_file`, `_evaluate_rule`, `_file_batch_worker`, `_instruction_worker`,
  `_collect_files`, `_read_ignore_file`, `_narrow_apply_to`, `_resolve_max_file_size`,
  `_is_text_file`, `_matches_pattern`, `_is_excluded`, `_split_patterns`,
  `_expand_brace_pattern`, `_load_secrets_allowlist`, `Violation`, `RuleResult`, `PatternCheck`)
- Delete: `guideline_checker/checker.py`, `guideline_checker/metrics.py`
- Modify (fix imports): `guideline_checker/cli.py`, `guideline_checker/proposer.py`,
  `guideline_checker/sandbox.py`, `guideline_checker/rule_health.py`,
  `guideline_checker/autofix.py`, `guideline_checker/fixers.py`
- Modify (re-point to new paths): every file under `tests/` that imports from
  `guideline_checker.checker` or `guideline_checker.metrics` — confirmed by grep below.

**Interfaces:**
- Consumes: nothing from earlier tasks beyond the empty `core/detection/__init__.py` from
  Task 1.
- Produces (for Tasks 3, 5, 6, 8 to import): `core.detection.run_checks(root, instructions,
  max_file_size=None) -> list[RuleResult]` (same signature `checker.run_checks` had — verify
  against the current signature before moving), `core.detection.Violation`,
  `core.detection.RuleResult`, `core.detection.pattern.PatternCheck`,
  `core.detection.numeric` (the module ex-`metrics.py`, same public names it had).

- [ ] **Step 1: Enumerate every current importer before moving anything**

```bash
grep -rln "from guideline_checker.checker import\|from guideline_checker import checker\|from guideline_checker.metrics import\|from guideline_checker import metrics" guideline_checker tests
```

Record the full file list from this command's output — every one of those files gets its
import statement rewritten in Step 5. Do not skip any file this command lists.

- [ ] **Step 2: Move the four already-generic files into `core/detection/` unchanged**

```bash
git mv guideline_checker/ast_python.py guideline_checker/core/detection/ast_python.py
git mv guideline_checker/ast_javascript.py guideline_checker/core/detection/ast_javascript.py
git mv guideline_checker/scanners.py guideline_checker/core/detection/scanners.py
git mv guideline_checker/scanner_source.py guideline_checker/core/detection/scanner_source.py
git mv guideline_checker/metrics.py guideline_checker/core/detection/numeric.py
```

- [ ] **Step 3: Split `checker.py` by copying function groups into new files, verbatim**

Create `core/detection/pattern.py` containing (copied verbatim, with their existing docstrings
and type hints, plus `from __future__ import annotations` and whatever imports each function
already used in `checker.py`): `PatternCheck`, `_split_patterns`, `_expand_brace_pattern`,
`_matches_pattern`, `_is_excluded`, `_compile_regex`, `_line_matches`, `_line_passes_regex`,
`_per_line_violations`, `_file_regex_violations`, `_require_regex_violations`.

Also copy the phrase-table dispatcher and every phrase family it calls into `pattern.py`
(confirmed by reading `checker.py`: `_build_checks` at line 899 is called from `_evaluate_rule`
at line 549 — the main per-rule dispatcher that lands in the orchestrator, Step 4 below — and
aggregates every phrase family into one `tuple[PatternCheck, ...]`; it belongs beside
`PatternCheck` itself, not in `presence.py`): `_build_checks`, `_debug_output_checks`,
`_exception_checks`, `_mentions`, `_dangerous_builtin_checks`, `_import_checks`,
`_annotation_checks`, `_hygiene_checks`, `_docker_checks`, `_typescript_checks`,
`_python_strict_checks`, `_security_checks`, `_django_checks`. These survive here only as long
as Task 3, which relocates them out of `pattern.py` into the `derive/` seed translator — do not
also copy them into `presence.py`, and do not skip copying them here on the assumption Task 3
will introduce them from scratch: Task 3's diff is a *move*, and it moves them from this exact
file.

Create `core/detection/crossref.py` containing: `_cross_reference_violations`,
`_definition_text`.

Create `core/detection/numeric.py` **additions** (merge into the file moved in Step 2, do not
overwrite it): `_measurement_text`, `_numeric_threshold_violations`, `_function_length_violation`,
`_check_function_lengths`, `_check_length_rules`.

Create `core/detection/presence.py` containing: `_check_presence_rules`, `_declared_violations`,
`_freshness_violations` — no phrase-family functions here (they all landed in `pattern.py`
above). If `_check_presence_rules` calls `_build_checks` or any phrase family internally (check
its body before copying), import that name from `.pattern` rather than duplicating it.

- [ ] **Step 4: Write the orchestrator in `core/detection/__init__.py`**

Move `Violation`, `RuleResult`, `run_checks`, `_check_file`, `_evaluate_rule`,
`_file_batch_worker`, `_instruction_worker`, `_collect_files`, `_read_ignore_file`,
`_narrow_apply_to`, `_resolve_max_file_size`, `_is_text_file`, `_load_secrets_allowlist`,
`_ast_violations`, `_scan_violations`, `_credential_scan_violations`,
`_is_hardcoded_credential_rule` here, importing the split-out helpers from `.pattern`,
`.crossref`, `.numeric`, `.presence`, `.ast_python`, `.ast_javascript`, `.scanners` as needed.

Also fold `guideline_checker/kinds.py` in here (spec §9 cut list: "the 'mechanisms-vs-values'
philosophy scaffolding (`kinds.py`, ...) — collapsed into the concrete kind registry; no
standalone abstraction"). Move `CheckKind`, `KIND_MEASURES`, `kind_of_detector`, `kind_of_phrase`
verbatim into `core/detection/__init__.py` (or a `core/detection/kinds.py` submodule imported
by `__init__.py`, if `__init__.py` would otherwise exceed the repo's own file-length rule —
check the current line-length threshold this repo enforces on itself, in the "Non-negotiable
conventions" section of `CLAUDE.md`, before deciding). Then:

```bash
git rm guideline_checker/kinds.py
git mv tests/test_kinds.py tests/test_core_detection_kinds.py
```

Fix `tests/test_core_detection_kinds.py`'s imports to the new location, and fix every other
importer (`grep -rln "from guideline_checker.kinds import\|from guideline_checker import
kinds" guideline_checker tests` — expect hits in `proposer.py`, `interpret.py`, or `persist.py`;
Task 8 will re-fix these same import lines when it moves those three files to `workshop/`, so a
transitional import to `guideline_checker.core.detection` here is correct and final).

- [ ] **Step 5: Delete `checker.py`, fix every importer from Step 1's list**

```bash
git rm guideline_checker/checker.py
```

For each file the Step 1 grep listed, replace:
- `from guideline_checker.checker import X` → `from guideline_checker.core.detection import X`
  (or `.pattern` / `.crossref` / `.numeric` / `.presence` — match to where X landed in Steps 3–4)
- `from guideline_checker.metrics import X` → `from guideline_checker.core.detection.numeric import X`

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: every test file that previously exercised `checker.py`/`metrics.py` (per `tests/`
filenames: `test_checker.py`, `test_metrics.py`, `test_cross_reference.py`,
`test_numeric_threshold.py`, `test_freshness.py`, `test_require_regex.py`, `test_exclude.py`,
`test_per_rule_exclude.py`) now imports from the new paths and passes with **zero** assertion
changes — a failure here means a function landed in the wrong file or an import was missed,
not that behavior changed.

- [ ] **Step 7: Update the boundary test's placeholder assertion**

In `tests/test_core_boundary.py`, replace
`test_boundary_test_covers_at_least_one_core_file_once_core_is_populated`'s body (`pass`) with:

```python
def test_boundary_test_covers_at_least_one_core_file_once_core_is_populated() -> None:
    assert len(_core_python_files()) >= 5, "core/detection/ should now contain multiple modules"
```

Run: `pytest tests/test_core_boundary.py -v` — Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(core): dissolve checker.py into core/detection/*"
```

---

### Task 3: Demote the phrase table to `core/derive/` (seed translator)

**Files:**
- Modify: `guideline_checker/core/detection/pattern.py` (delete `_build_checks` and the phrase
  families once Step 3 relocates their logic — this stale header used to say `presence.py`,
  corrected during Task 2's implementation: the phrase table landed in `pattern.py`, not
  `presence.py`; there is no `# TODO(Task 3)` marker anywhere, per Task 2's actual output)
- Create: `guideline_checker/core/derive/seed.py` (the relocated phrase functions, changed to
  return in-memory YAML-shaped rules instead of `PatternCheck` tuples)
- Modify: `guideline_checker/core/derive/__init__.py` (exposes `derive_seed_rules`)
- Test: `tests/test_derive_seed.py`
- Modify: `tests/test_checker.py` → rename references (phrase-table behavior is now tested via
  the derive path; existing assertions on violation output must still hold end-to-end)

**Interfaces:**
- Consumes: `guideline_checker.loader.RuleDetector` (existing dataclass — verify its field names
  by reading `guideline_checker/loader.py` before writing `_to_detector` below; do not assume).
- Produces: `derive.seed.derive_seed_rules(prose_rule: str) -> RuleDetector | None` — `None`
  when no phrase in the table matches; a populated `RuleDetector` (pattern-kind fields only)
  otherwise. Task 4 and Task 6 call this as the heuristic-first step of the generation loop.

- [ ] **Step 1: Write the failing test for one migrated phrase family**

```python
"""Tests for core/derive/seed.py — the phrase-table-as-heuristic seed translator."""

from guideline_checker.core.derive.seed import derive_seed_rules


def test_no_print_phrase_derives_a_forbid_pattern_detector() -> None:
    detector = derive_seed_rules("No print statements in production code")
    assert detector is not None
    assert "print(" in detector.forbid


def test_unrecognised_prose_derives_nothing() -> None:
    assert derive_seed_rules("Prefer composition over inheritance") is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_derive_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'guideline_checker.core.derive.seed'`.

- [ ] **Step 3: Move the phrase functions into `core/derive/seed.py`, changed to emit `RuleDetector`**

Relocate from `core/detection/pattern.py` (where Task 2 parked them — **not** `presence.py`;
`_build_checks` and its phrase families are pattern-table code, kept beside `PatternCheck` in
Task 2 specifically so this task moves them once, from one place):
`_build_checks` (dissolved — its body becomes this step's `derive_seed_rules`, not a
verbatim copy; see below), `_debug_output_checks`, `_exception_checks`, `_mentions`,
`_dangerous_builtin_checks`, `_import_checks`, `_annotation_checks`, `_hygiene_checks`,
`_docker_checks`, `_is_hardcoded_credential_rule` (keep only the boolean check here; the scan
itself stays in `core/detection/scanners.py`), `_typescript_checks`, `_python_strict_checks`,
`_security_checks`, `_django_checks`. Each currently returns `tuple[PatternCheck, ...]`; change
each to return `RuleDetector | None` built from the same forbid/forbid_regex phrase table
(read the exact `RuleDetector` field names from `guideline_checker/loader.py` first). Add the
public entry point:

```python
def derive_seed_rules(prose_rule: str) -> RuleDetector | None:
    """Turn a recognised prose sentence into an in-memory pattern detector.

    Returns ``None`` when no phrase family in the table recognises the sentence —
    the caller (core/derive, Task 4) then falls back to the LLM path if [workshop]
    is installed, or leaves the rule advisory otherwise.
    """
    rule_lower = prose_rule.lower()
    for family in (
        _debug_output_checks, _exception_checks, _dangerous_builtin_checks,
        _import_checks, _annotation_checks, _hygiene_checks, _docker_checks,
        _typescript_checks, _python_strict_checks, _security_checks, _django_checks,
    ):
        if detector := family(rule_lower):
            return detector
    return None
```

- [ ] **Step 4: Run the new test to confirm it passes**

Run: `pytest tests/test_derive_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Replace the phrase dispatch call with a direct (uncached) seed-translator call**

In `core/detection/__init__.py`'s `_evaluate_rule` (the function that, per Task 2 Step 1's
grep-confirmed call graph, calls `_build_checks(rule_lower)` at the old `checker.py` line 549),
replace `checks = _build_checks(rule_lower)` with a call into the seed translator whenever the
instruction carries no declarative `detector`:

```python
from guideline_checker.core.derive.seed import derive_seed_rules
...
detector = instruction.detector or derive_seed_rules(instruction.rule)
```

then feed `detector`'s pattern fields into the same per-line/whole-file check path Task 2 built
in `pattern.py` (`_per_line_violations`/`_file_regex_violations`, which already consume a
`RuleDetector`-shaped input — verify the exact call signature `_evaluate_rule` used before this
change and preserve it). This direct, uncached call is intentionally temporary: it keeps
Task 3's own migration kill-test (Step 6 below) green without waiting on Task 6. Task 6 replaces
this in-line call with the cache-aware `resolve_rule_detectors` pre-pass and removes the
`derive_seed_rules(instruction.rule)` fallback from inside `_evaluate_rule` at that point — leave
a `# TODO(Task 6): replace with cached resolve_rule_detectors pre-pass` comment on this line so
Task 6 has an exact target. Delete `_build_checks` and the phrase-family functions from
`core/detection/pattern.py` now that Step 3 relocated their logic into `derive/seed.py`.

- [ ] **Step 5b: Fix `proposer.py`'s now-broken import of the deleted `_build_checks`**

Task 2 pointed `guideline_checker/proposer.py`'s `from guideline_checker.checker import
_build_checks` at `from guideline_checker.core.detection.pattern import _build_checks` (Task 2
Step 5's blanket importer fix). This step's deletion of `_build_checks` from `pattern.py` breaks
that import. Fix it now: change `proposer.py`'s import to
`from guideline_checker.core.derive.seed import derive_seed_rules` and adapt its call site from
`_build_checks(rule_lower)` to `derive_seed_rules(rule)`, matching the signature change Step 3
made. (`proposer.py` still lives at its pre-Task-8 path here — Task 8 only moves the file to
`workshop/`, it does not touch this particular import again.)

- [ ] **Step 6: Run the full suite — this is the spec's migration kill-test**

Run: `pytest -q`
Expected: `tests/test_checker.py`'s phrase-table assertions (e.g. "no print" → violation) still
pass, now routed through `derive_seed_rules` feeding a `RuleDetector` into the same
`core.detection` pattern-matching path Task 2 built — same violations, different internal path.
A failure here means the seed translator lost coverage the old phrase table had; fix the
translator, not the test.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(derive): demote phrase table to core/derive/seed.py translator"
```

---

### Task 4: `core/derive/cache.py` — local ephemeral derived-detector cache

**Files:**
- Create: `guideline_checker/core/derive/cache.py`
- Test: `tests/test_derive_cache_v2.py` (distinct name from the existing
  `tests/test_derived_cache.py`, which covers the unrelated workshop `interpret`/`persist`
  repo-tree cache — do not conflate the two; both survive)
- Modify: `.gitignore` (add `.guideline-cache/`)

**Interfaces:**
- Produces: `cache.cache_path(root: Path) -> Path` (honours `GUIDELINE_CACHE_DIR` env var, else
  `root / ".guideline-cache"`), `cache.load(root: Path, prose_hash: str) -> RuleDetector | None`,
  `cache.store(root: Path, prose_hash: str, detector: RuleDetector) -> None`,
  `cache.prose_hash(prose: str, engine_version: str) -> str`. Task 6 calls all four.

- [ ] **Step 1: Write the failing determinism test**

```python
"""Tests for core/derive/cache.py — local ephemeral cache (spec §6 determinism claim)."""

import os
from pathlib import Path

from guideline_checker.core.derive.cache import cache_path, load, prose_hash, store
from guideline_checker.loader import RuleDetector


def test_prose_hash_is_deterministic_for_same_inputs() -> None:
    assert prose_hash("No print", "1.0.0") == prose_hash("No print", "1.0.0")


def test_prose_hash_changes_when_engine_version_changes() -> None:
    assert prose_hash("No print", "1.0.0") != prose_hash("No print", "1.0.1")


def test_store_then_load_round_trips(tmp_path: Path) -> None:
    detector = RuleDetector(forbid=("print(",))
    key = prose_hash("No print", "1.0.0")
    store(tmp_path, key, detector)
    assert load(tmp_path, key) == detector


def test_load_returns_none_on_cache_miss(tmp_path: Path) -> None:
    assert load(tmp_path, "unknown-key") is None


def test_cache_path_honours_env_override(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom-cache"
    monkeypatch.setenv("GUIDELINE_CACHE_DIR", str(override))
    assert cache_path(tmp_path) == override
    monkeypatch.delenv("GUIDELINE_CACHE_DIR")
    assert cache_path(tmp_path) == tmp_path / ".guideline-cache"
```

- [ ] **Step 2: Run to confirm it fails**

Run: `pytest tests/test_derive_cache_v2.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'guideline_checker.core.derive.cache'`.

- [ ] **Step 3: Implement**

```python
"""Local ephemeral derived-detector cache (spec §3.4).

Git-ignored, keyed by hash(prose + engine version). Warm runs are fast; a cold run
(cache dir absent, or a prose hash miss) re-derives. `check` writes only this
directory, never the repo tree — the repo-tree derived cache workshop/persist.py
writes is a separate, explicit, user-triggered mechanism (ADR D-0016).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from guideline_checker.loader import RuleDetector

_ENV_OVERRIDE = "GUIDELINE_CACHE_DIR"
_DEFAULT_DIRNAME = ".guideline-cache"


def cache_path(root: Path) -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    return Path(override) if override else root / _DEFAULT_DIRNAME


def prose_hash(prose: str, engine_version: str) -> str:
    digest = hashlib.sha256()
    digest.update(prose.encode("utf-8"))
    digest.update(b"\0")
    digest.update(engine_version.encode("utf-8"))
    return digest.hexdigest()


def load(root: Path, key: str) -> RuleDetector | None:
    entry = cache_path(root) / f"{key}.json"
    if not entry.exists():
        return None
    data = json.loads(entry.read_text(encoding="utf-8"))
    return RuleDetector(**data)


def store(root: Path, key: str, detector: RuleDetector) -> None:
    directory = cache_path(root)
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / f"{key}.json"
    entry.write_text(json.dumps(detector.__dict__), encoding="utf-8")
```

Before finalizing, confirm `RuleDetector.__dict__` round-trips through `json.dumps`/`RuleDetector(**data)` — if any field is a non-JSON type (e.g. `re.Pattern` or a `tuple` that needs explicit list→tuple coercion on load), adjust `store`/`load` to serialise/deserialise that field explicitly rather than assuming a bare round-trip works. Check `guideline_checker/loader.py`'s `RuleDetector` field types first.

- [ ] **Step 4: Run to confirm it passes**

Run: `pytest tests/test_derive_cache_v2.py -v`
Expected: PASS.

- [ ] **Step 5: Git-ignore the cache directory**

In `.gitignore`, add near the other local-tooling caches (after the `.import_linter_cache/`
line around line 266):

```gitignore
.guideline-cache/
```

- [ ] **Step 6: Run the full suite and commit**

Run: `pytest -q`

```bash
git add -A
git commit -m "feat(derive): local ephemeral cache for derived detectors"
```

---

### Task 5: Relocate `rule_health.py` → `core/health.py`, fold in `sandbox.py`

**Files:**
- Create: `guideline_checker/core/health.py` (merge of `rule_health.py` + `sandbox.py`)
- Delete: `guideline_checker/rule_health.py`, `guideline_checker/sandbox.py`
- Modify (fix imports): `guideline_checker/cli.py`, `guideline_checker/web/app.py`,
  `guideline_checker/proposer.py` (if it imports `sandbox`), any file the grep in Step 1 finds
- Modify: `tests/test_rule_health.py`, `tests/test_sandbox.py` → merge into
  `tests/test_core_health.py` (keep every existing test case; only the import path and module
  name change)

**Interfaces:**
- Consumes: `core.detection.RuleResult` (Task 2), `guideline_checker.loader.RuleDetector`.
- Produces: everything `rule_health.py` exposed today (`HealthState`, `RuleHealth`,
  `compute_rule_health`, `summarize`) **plus** whatever `sandbox.py` exposed (read
  `guideline_checker/sandbox.py`'s public names before merging — do not guess them; copy them
  verbatim as new top-level functions in `core/health.py`). This is what Task 6 and Task 8's
  `workshop/proposer.py` import.

- [ ] **Step 1: Enumerate every importer of the two source modules**

```bash
grep -rln "from guideline_checker.rule_health import\|from guideline_checker import rule_health\|from guideline_checker.sandbox import\|from guideline_checker import sandbox" guideline_checker tests
```

- [ ] **Step 2: Read both source files in full before merging**

Read `guideline_checker/rule_health.py` and `guideline_checker/sandbox.py` completely. Confirm
there is no name collision between the two (e.g. both must not define a function or class with
the same name) before copying their contents into one file — if a collision exists, rename the
`sandbox.py` symbol with a `sandbox_`-prefixed name and note the rename in this task's commit
message.

- [ ] **Step 3: Create `core/health.py` as the union of both files' contents**

```bash
git mv guideline_checker/rule_health.py guideline_checker/core/health.py
```

Append `sandbox.py`'s full contents (imports merged, no duplicate `from __future__ import
annotations`) to the bottom of `core/health.py`, with a module-level docstring note:

```python
# The proof routine below was guideline_checker/sandbox.py before the v2 redesign
# (spec §4: "health proves it in sandbox — core sandbox path, read-only"). It has
# no LLM/gh dependency, so it is core-owned; workshop/proposer.py imports it from
# here rather than keeping its own copy (satellite → core, never the reverse).
```

```bash
git rm guideline_checker/sandbox.py
```

- [ ] **Step 4: Fix every importer from Step 1's list**

Replace `from guideline_checker.rule_health import X` and
`from guideline_checker.sandbox import Y` with `from guideline_checker.core.health import X, Y`
in every file the Step 1 grep listed, including `guideline_checker/proposer.py` if it appears.

- [ ] **Step 5: Merge the two test files**

```bash
git mv tests/test_rule_health.py tests/test_core_health.py
```

Append every test function from `tests/test_sandbox.py` into `tests/test_core_health.py`
(rename on collision only, per Step 2's finding), fixing their imports to
`guideline_checker.core.health`. Then:

```bash
git rm tests/test_sandbox.py
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, same total assertion count as `test_rule_health.py` + `test_sandbox.py` had
before the merge — verify by diffing `pytest --collect-only tests/test_core_health.py` test IDs
against the union of the two old files' test IDs (captured before Step 3).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(core): merge rule_health.py + sandbox.py into core/health.py"
```

---

### Task 6: Wire the generation loop into `check`; health matrix leads the report

**Files:**
- Modify: `guideline_checker/cli.py` (`_cmd_check`, currently at line ~530)
- Modify: `guideline_checker/core/detection/__init__.py` (`run_checks` gains a pre-pass)
- Modify: `guideline_checker/core/health.py` (health becomes the loop's proof gate)
- Test: `tests/test_generation_loop.py`

**Interfaces:**
- Consumes: `core.derive.seed.derive_seed_rules` (Task 3), `core.derive.cache.{cache_path,
  prose_hash, load, store}` (Task 4), `core.health.compute_rule_health` and the folded-in proof
  function (Task 5), `core.detection.run_checks` (Task 2).
- Produces: `core.detection.resolve_rule_detectors(root: Path, instructions: list[InstructionFile], engine_version: str) -> list[InstructionFile]` — same instruction files, with any rule that had no `detect:` block and a matching seed translation now carrying a proven `RuleDetector`, sourced cache-first. `_cmd_check` calls this before `run_checks`.

- [ ] **Step 1: Write the failing test for cache-first resolution**

```python
"""Tests for the generation loop: derive -> prove -> cache -> detect (spec §3.3)."""

from pathlib import Path

from guideline_checker.core.detection import resolve_rule_detectors
from guideline_checker.core.derive.cache import prose_hash, store
from guideline_checker.loader import InstructionFile, RuleDetector

_ENGINE_VERSION = "test-1.0.0"


def _instruction(rule: str) -> InstructionFile:
    return InstructionFile(source="CLAUDE.md", rule=rule, detector=None, apply_to=("**",))


def test_cache_hit_is_used_without_rederiving(tmp_path: Path) -> None:
    rule = "No print statements in production code"
    key = prose_hash(rule, _ENGINE_VERSION)
    store(tmp_path, key, RuleDetector(forbid=("cached-marker",)))

    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)

    assert resolved[0].detector is not None
    assert "cached-marker" in resolved[0].detector.forbid


def test_cache_miss_derives_and_stores(tmp_path: Path) -> None:
    rule = "No print statements in production code"
    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)

    assert resolved[0].detector is not None
    assert "print(" in resolved[0].detector.forbid
    # second call must now hit the cache Task 4's store() wrote
    key = prose_hash(rule, _ENGINE_VERSION)
    from guideline_checker.core.derive.cache import load
    assert load(tmp_path, key) is not None


def test_unrecognised_prose_stays_advisory(tmp_path: Path) -> None:
    rule = "Prefer composition over inheritance"
    resolved = resolve_rule_detectors(tmp_path, [_instruction(rule)], _ENGINE_VERSION)
    assert resolved[0].detector is None
```

Before writing this, read `guideline_checker/loader.py`'s `InstructionFile` field names — the
`_instruction` helper above assumes `source`, `rule`, `detector`, `apply_to`; correct them to
match the real dataclass if different.

- [ ] **Step 2: Run to confirm it fails**

Run: `pytest tests/test_generation_loop.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_rule_detectors'`.

- [ ] **Step 3: Implement `resolve_rule_detectors` — but do NOT remove Task 3's supplementary seed call**

**Ruling (controller, post-Task-3 review):** Task 3's actual implementation of
`_evaluate_rule` does not do a simple `detector = instruction.detector or
derive_seed_rules(instruction.rule)` fallback — it runs the seed check *in addition to* the
declared detector and merges their violations, because a real pre-existing test
(`tests/test_guidelines.py::TestRuleInheritance::test_abstract_scalar_only_template`) ships a
rule with a declared `forbid: ["TODO"]` detector (no `match_in_comments`) whose prose *also*
matches the seed phrase table's `_hygiene_checks` (which sets `match_in_comments=True`) — the
declared detector alone can't see `# TODO fix` in a comment, and this only worked before
Task 3 because the phrase path ran *alongside* the declared one, not instead of it. This is
correct, permanent behavior, not an interim shim: **leave Task 3's supplementary
`derive_seed_rules(instruction.rule)` call in `_evaluate_rule` in place.** Update or remove its
`# TODO(Task 6)` comment to instead read `# supplementary seed check — always runs alongside
instruction.detector, see D-0024 / test_abstract_scalar_only_template` so a future reader
doesn't delete it by mistake.

What Task 6 actually adds is narrower than the original brief text implied: `resolve_rule_detectors`
below only handles the case where `instruction.detector is None` (no declared YAML detector at
all) — it fills that gap from the cache-or-derive path so a *primary* detector exists where
possible, cached for reuse. It does not touch or replace `_evaluate_rule`'s always-on
supplementary seed call, which keeps combining with whatever primary detector ends up set
(YAML-declared, cache-filled, or still `None`). This split is intentional: the cache exists for
future non-deterministic (LLM, `[workshop]`) derivation reuse (spec §3.4), not because today's
deterministic `derive_seed_rules` needs caching for its own sake — so it is correct for the
always-on supplementary path to stay direct and uncached while the primary-detector-filling
path gets the cache.

```python
def resolve_rule_detectors(
    root: Path, instructions: list[InstructionFile], engine_version: str
) -> list[InstructionFile]:
    """Fill in a missing detector per instruction via cache-first heuristic derivation.

    Cache hit -> reuse. Cache miss -> derive_seed_rules(); a hit is stored and used,
    a miss leaves the instruction's detector as None (advisory; spec §3.3 step 4).
    """
    resolved = []
    for instruction in instructions:
        if instruction.detector is not None:
            resolved.append(instruction)
            continue
        key = prose_hash(instruction.rule, engine_version)
        cached = load(root, key)
        if cached is not None:
            resolved.append(dataclasses.replace(instruction, detector=cached))
            continue
        derived = derive_seed_rules(instruction.rule)
        if derived is not None:
            store(root, key, derived)
        resolved.append(dataclasses.replace(instruction, detector=derived))
    return resolved
```

Add the needed imports (`dataclasses`, `from guideline_checker.core.derive.seed import
derive_seed_rules`, `from guideline_checker.core.derive.cache import load, prose_hash, store`).
Confirm `InstructionFile` supports `dataclasses.replace` (it must be a `@dataclass`) before
using it — check `loader.py`.

- [ ] **Step 4: Run to confirm it passes**

Run: `pytest tests/test_generation_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Call `resolve_rule_detectors` from `_cmd_check`, and lead reports with health**

In `guideline_checker/cli.py`'s `_cmd_check` (around line 530), after instructions are loaded
and before `run_checks` is called, insert:

```python
instructions = resolve_rule_detectors(root, instructions, _ENGINE_VERSION)
```

(Define `_ENGINE_VERSION` at module level in `cli.py`, sourced from
`guideline_checker.__version__` if that exists — check `guideline_checker/__init__.py` first;
if no version constant exists yet, add one there and import it.)

Then, before invoking any reporter, compute and pass the health matrix first: read the current
reporter call order in `_cmd_check` and reorder so `compute_rule_health(...)` (from
`core/health.py`) is computed and handed to each reporter ahead of the violation list — check
each reporter's current function signature in `guideline_checker/reporters/*.py` (`html.py`,
`markdown.py`, `json_reporter.py`, `sarif.py`) before changing call order, since some may already
accept a `health` parameter (Task 5 did not change reporter signatures) — if a reporter does not
yet accept health data, that reporter's signature update belongs to this task, not a later one:
add a `health: list[RuleHealth]` parameter and render it first in `html.py`/`markdown.py`'s
output-building code.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS. `tests/test_cli.py`, `tests/test_html_reporter.py`,
`tests/test_markdown_reporter.py`, `tests/test_json_reporter.py` may need their expected-output
fixtures updated to reflect the health-matrix-first ordering — update fixtures, not assertions
of health data correctness.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(core): wire generation loop into check; lead reports with rule health"
```

---

### Task 7: `guideline-checker health` standalone subcommand

**Files:**
- Modify: `guideline_checker/cli.py` (new `health_cmd = sub.add_parser("health", ...)` near the
  other `add_parser` calls at the top of the argument-parsing function; new `_cmd_health`)
- Test: `tests/test_cli.py` (add health-subcommand cases; do not create a new test file — this
  repo's convention is one `test_cli.py` per CLI, confirmed by the existing file holding all
  other subcommand tests)

**Interfaces:**
- Consumes: `core.health.compute_rule_health`, `core.detection.resolve_rule_detectors`.
- Produces: exit code 0 always (health is informational, spec §3.5 — it is "the sales pitch",
  not a gate); stdout output listing counts per `HealthState`.

- [ ] **Step 1: Write the failing test**

```python
def test_health_command_reports_state_counts(tmp_path, capsys):
    (tmp_path / "CLAUDE.md").write_text("No print statements.\n", encoding="utf-8")
    exit_code = main(["health", "--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "proven" in captured.out.lower()
```

Add this to `tests/test_cli.py`, matching the existing test file's fixture/import conventions
(read the top of `tests/test_cli.py` first — it already defines a `main` import and a
tmp-project fixture pattern other subcommand tests reuse).

- [ ] **Step 2: Run to confirm it fails**

Run: `pytest tests/test_cli.py -k health_command -v`
Expected: FAIL — `argparse` error, unrecognised subcommand `health`.

- [ ] **Step 3: Implement**

In `cli.py`, alongside the other `add_parser` calls:

```python
health_cmd = sub.add_parser("health", help="Report rule-health only — no violation scan.")
health_cmd.add_argument("--root", type=Path, default=Path("."))
health_cmd.set_defaults(func=_cmd_health)
```

```python
def _cmd_health(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    instructions = load_all_sources(root)  # same loader call _cmd_check already makes
    instructions = resolve_rule_detectors(root, instructions, _ENGINE_VERSION)
    health = compute_rule_health(instructions, results=[])
    counts = summarize(health)
    for state, count in counts.items():
        print(f"{state}: {count}")
    return 0
```

Verify `compute_rule_health`'s real signature in `core/health.py` before writing this call —
Task 5 preserved it verbatim from `rule_health.py`, so read that signature, don't assume
`results=[]` is accepted; adjust the call to match.

- [ ] **Step 4: Run to confirm it passes**

Run: `pytest tests/test_cli.py -k health_command -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -q`

```bash
git add -A
git commit -m "feat(cli): add standalone 'guideline-checker health' subcommand"
```

---

### Task 8: Relocate workshop modules; lazy-load workshop routes from `web/app.py`

**Files:**
- Move: `guideline_checker/proposer.py` → `guideline_checker/workshop/proposer.py`
- Move: `guideline_checker/persist.py` → `guideline_checker/workshop/persist.py`
- Move: `guideline_checker/interpret.py` → `guideline_checker/workshop/interpret.py`
- Create: `guideline_checker/workshop/web_endpoints.py` (extracted FastAPI router)
- Modify: `guideline_checker/web/app.py` (remove the extracted routes; lazy-mount the router)
- Modify: every importer the grep in Step 1 finds
- Modify: `tests/test_proposer.py`, `tests/test_proposer_claude.py`, `tests/test_proposer_llm.py`,
  `tests/test_persist.py`, `tests/test_interpret.py`, `tests/test_web.py` (import paths only)

**Interfaces:**
- Consumes: `core.health` (Task 5, for the proof function `proposer.py` needs), `core.derive.seed`
  if `proposer.py` calls the heuristic path as its own first attempt (read `proposer.py` before
  moving to confirm whether it already does this or whether it is LLM-only).
- Produces: `workshop.web_endpoints.router: fastapi.APIRouter` — a FastAPI `APIRouter` carrying
  every route `web/app.py` currently defines under `/api/interpret*`, `/api/propose`,
  `/api/rules/detector`, `/api/rules/resolve` (per the grep of `web/app.py`'s route decorators
  already performed during planning — lines ~517–757).

- [ ] **Step 1: Enumerate every importer of the three modules**

```bash
grep -rln "from guideline_checker.proposer import\|from guideline_checker.persist import\|from guideline_checker.interpret import\|from guideline_checker import proposer\|from guideline_checker import persist\|from guideline_checker import interpret" guideline_checker tests
```

- [ ] **Step 2: Move the three files, fixing their internal imports**

```bash
git mv guideline_checker/proposer.py guideline_checker/workshop/proposer.py
git mv guideline_checker/persist.py guideline_checker/workshop/persist.py
git mv guideline_checker/interpret.py guideline_checker/workshop/interpret.py
```

`proposer.py`'s import of the phrase translator was already fixed to
`from guideline_checker.core.derive.seed import derive_seed_rules` by Task 3 Step 5b — this
task only relocates the file itself; no further change to that particular import is needed
here. Just confirm it after the move: `grep -n "derive.seed\|_build_checks" workshop/proposer.py`
should show only the `core.derive.seed` import, never `_build_checks`.

- [ ] **Step 3: Fix every other importer from Step 1's list**

Replace `from guideline_checker.proposer import X` → `from guideline_checker.workshop.proposer
import X` (same pattern for `persist`, `interpret`) in every file Step 1 listed.

- [ ] **Step 4: Extract the propose/interpret/persist routes from `web/app.py`**

Read `guideline_checker/web/app.py` lines 517–757 in full. Create
`guideline_checker/workshop/web_endpoints.py`:

```python
"""Workshop web routes — propose/prove/persist a detector from the UI (spec §3.1, [workshop])."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from guideline_checker.web.auth import require_auth

router = APIRouter()

# ... the exact route bodies currently at web/app.py lines 517-757, moved verbatim,
# decorated with @router.get / @router.post instead of @app.get / @app.post.
```

Move every route currently decorated at those line numbers verbatim into this router, changing
only the decorator target (`@app.get(...)` → `@router.get(...)`, same for `.post`). Remove them
from `web/app.py`.

- [ ] **Step 5: Mount the router lazily from `web/app.py`**

At the point in `web/app.py` where routes are registered (after `app = FastAPI(...)`), add:

```python
try:
    from guideline_checker.workshop.web_endpoints import router as _workshop_router
except ImportError:
    _workshop_router = None

if _workshop_router is not None:
    app.include_router(_workshop_router)
```

This is the locked plugin mechanism from Global Constraints: `web/app.py` (a `web`-extra module)
never hard-depends on `workshop/`; the propose/interpret/persist UI panel simply does not mount
if `workshop/` cannot be imported (today it always can, since it has no third-party deps beyond
stdlib — this guards the *future* case where it gains one).

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS. `tests/test_web.py` must still find the `/api/propose` etc. routes registered
(FastAPI's `TestClient` hits the same `app` object; router inclusion is transparent to it) —
if any test imports route handler functions directly rather than through the `TestClient`, fix
those imports to `guideline_checker.workshop.web_endpoints`.

- [ ] **Step 7: Run the boundary test explicitly**

Run: `pytest tests/test_core_boundary.py -v`
Expected: PASS — confirms Steps 2–5 did not introduce a `core/` → `workshop/` import by mistake.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(workshop): relocate proposer/persist/interpret; lazy-mount workshop web routes"
```

---

### Task 9: Relocate fleet modules

**Files:**
- Move: `guideline_checker/gh_client.py`, `distribution.py`, `manifest.py`, `origin_audit.py`,
  `lifecycle.py` → `guideline_checker/fleet/`
- Modify: `guideline_checker/cli.py` (`_cmd_synthesize_origin` and any other fleet call site)
- Modify: every importer the grep in Step 1 finds
- Modify: `tests/test_gh_client.py`, `tests/test_distribution.py`, `tests/test_manifest.py`,
  `tests/test_origin_audit.py`, `tests/test_lifecycle.py` (import paths only)

**Interfaces:**
- Produces: `fleet.gh_client.GhClient`, `fleet.distribution`, `fleet.manifest`,
  `fleet.origin_audit`, `fleet.lifecycle` — same public names each module had, just at the new
  path. `cli.py`'s `_cmd_synthesize_origin` is the only core-side caller; it must import fleet
  lazily (same pattern as Task 8 Step 5) since `cli.py` is otherwise a `core/`-adjacent module
  reachable with no extras installed.

- [ ] **Step 1: Enumerate every importer**

```bash
grep -rln "from guideline_checker.gh_client import\|from guideline_checker.distribution import\|from guideline_checker.manifest import\|from guideline_checker.origin_audit import\|from guideline_checker.lifecycle import\|from guideline_checker import gh_client\|from guideline_checker import distribution\|from guideline_checker import manifest\|from guideline_checker import origin_audit\|from guideline_checker import lifecycle" guideline_checker tests
```

- [ ] **Step 2: Move the five files**

```bash
git mv guideline_checker/gh_client.py guideline_checker/fleet/gh_client.py
git mv guideline_checker/distribution.py guideline_checker/fleet/distribution.py
git mv guideline_checker/manifest.py guideline_checker/fleet/manifest.py
git mv guideline_checker/origin_audit.py guideline_checker/fleet/origin_audit.py
git mv guideline_checker/lifecycle.py guideline_checker/fleet/lifecycle.py
```

Fix any import between these five files that referenced a sibling by its old top-level path
(e.g. `from guideline_checker.gh_client import GhClient` inside `distribution.py`) to
`from guideline_checker.fleet.gh_client import GhClient`.

- [ ] **Step 3: Fix every other importer from Step 1's list, lazily in `cli.py`**

In `cli.py`'s `_cmd_synthesize_origin`, the current top-of-file import of `gh_client`/
`origin_audit`/etc. must become a lazy import **inside the function body**, not a module-level
import — this is what keeps `cli.py` (always importable, no extras) from requiring `fleet/`'s
transitive deps once `fleet` gains one in the future:

```python
def _cmd_synthesize_origin(args: argparse.Namespace) -> int:
    from guideline_checker.fleet import distribution, origin_audit  # lazy: [fleet] boundary
    ...
```

For every other importer Step 1 listed that is not `cli.py` (e.g. a test file), a plain
module-level `from guideline_checker.fleet.X import Y` is fine — the lazy-import requirement is
specific to `cli.py`'s default (no-extras) entry point.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 5: Run the boundary test explicitly**

Run: `pytest tests/test_core_boundary.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(fleet): relocate gh_client/distribution/manifest/origin_audit/lifecycle"
```

---

### Task 10: Delete `central.py`, `push`, `web/central` (spec §9 cut list)

**Files:**
- Delete: `guideline_checker/web/central.py`, `tests/test_central.py`
- Modify: `guideline_checker/cli.py` (remove `central_cmd`/`push_cmd` parsers at lines ~303 and
  ~332, and `_cmd_central`/`_cmd_push` at lines ~928 and ~1054 — line numbers are from the
  pre-Task-2 file; re-locate by the parser variable names, not the numbers, since prior tasks
  shifted line numbers)
- Modify: `README.md`, `CLAUDE.md` (remove any usage section documenting `central`/`push`)

**Interfaces:** none — pure deletion, no other module depends on `central.py` (confirmed
earlier: `web/app.py` does not import it; only `cli.py`'s `push` command posts to it over HTTP).

- [ ] **Step 1: Confirm no other importer exists**

```bash
grep -rln "web.central\|web import central\|_cmd_central\|_cmd_push" guideline_checker tests README.md CLAUDE.md
```

Every hit this returns is one this task must edit or remove — do not proceed past this step
until you have the full list.

- [ ] **Step 2: Delete the module and its test**

```bash
git rm guideline_checker/web/central.py tests/test_central.py
```

- [ ] **Step 3: Remove the CLI wiring**

In `cli.py`, delete the `central_cmd = sub.add_parser(...)` block, the `push_cmd =
sub.add_parser(...)` block, and the `_cmd_central` and `_cmd_push` function definitions in full.

- [ ] **Step 4: Update docs**

In `README.md` and `CLAUDE.md`, delete any paragraph documenting `guideline-checker central` or
`guideline-checker push` usage (grep for `central` / `push` in both files first to find the
exact sections — do not assume a fixed line range since Task 12 will also touch `CLAUDE.md`).

- [ ] **Step 5: Run the full suite and commit**

Run: `pytest -q`
Expected: PASS, with `tests/test_central.py`'s prior test count subtracted from the total —
confirm this is the only count change.

```bash
git add -A
git commit -m "feat(cli)!: remove central.py, push and web/central (v2.0.0 breaking change)"
```

---

### Task 11: Full regression pass — re-point remaining stragglers, run `docker-test`

**Files:** whichever test files remain unaddressed by Tasks 2–10 (this task is the safety net,
not a specific-file task).

**Interfaces:** none new — this task only verifies.

- [ ] **Step 1: Repo-wide grep for any remaining old-path import**

```bash
grep -rn "from guideline_checker\.\(checker\|metrics\|rule_health\|sandbox\|proposer\|persist\|interpret\|gh_client\|distribution\|manifest\|origin_audit\|lifecycle\) import\|from guideline_checker import \(checker\|metrics\|rule_health\|sandbox\|proposer\|persist\|interpret\|gh_client\|distribution\|manifest\|origin_audit\|lifecycle\)" guideline_checker tests scripts
```

Expected: no output. Any hit is a straggler import Tasks 2–9 missed — fix it now, in the file
it appears in, using the same old-path → new-path mapping those tasks used.

- [ ] **Step 2: Run the full unit/integration suite with coverage**

Run: `pytest -q --cov=guideline_checker --cov-report=term-missing`
Expected: PASS, coverage ≥ 85% (the existing `--cov-fail-under=85` gate in `pyproject.toml`).

- [ ] **Step 3: Run the authoritative Docker-based test gate**

Run: `make docker-test` (or the repo's documented equivalent — check `Makefile` for the exact
target name before running, since `make` target names are project-specific and must not be
guessed)
Expected: PASS.

- [ ] **Step 4: Run the CI-reproducibility check from spec §6**

Manually verify: with no `[workshop]` extra semantics active (there are none today, since
workshop has no extra deps — this check becomes meaningful once one is added; for now, confirm
`pytest tests/test_generation_loop.py::test_unrecognised_prose_stays_advisory -v` passes twice
in a row with identical output, standing in for the reproducibility guarantee until a real LLM
dependency exists to gate).

- [ ] **Step 5: Commit any straggler fixes**

```bash
git add -A
git commit -m "fix: re-point remaining stragglers to core/workshop/fleet module paths"
```

(Skip this commit if Step 1 found nothing to fix.)

---

### Task 12: Shrink `CLAUDE.md`; archive obsolete ADRs; add D-0024

**Files:**
- Modify: `CLAUDE.md` (replace the "Non-negotiable conventions" section, lines 262–1139 in the
  pre-Task-12 file — re-locate by the `## Non-negotiable conventions` heading, not the line
  number, since this file may have shifted)
- Modify: `DECISIONS.md` (add D-0024; mark obsolete meta-layer ADRs archived)

**Interfaces:** none — documentation only.

- [ ] **Step 1: Confirm the section is a verbatim copy of the shared standard**

```bash
diff <(sed -n '/^## Non-negotiable conventions/,/^## Quality gates/p' CLAUDE.md) \
     <(sed -n '/^## Non-negotiable conventions/,/^## Quality gates/p' ~/Documents/perso/projects/chrysa/shared-standards/CLAUDE.md)
```

If this diff is empty or near-empty, the section is confirmed to be the fleet-wide constitution
inlined verbatim (matches the problem statement in the spec). If the diff is large, stop and
re-scope this task — do not delete content that turns out to be guideline-checker-specific
without re-reading it first.

- [ ] **Step 2: Replace the section with a by-reference pointer plus repo-specific deltas**

Read the full "Non-negotiable conventions" section once to identify which sub-bullets (if any)
are guideline-checker-specific rather than fleet-wide boilerplate (spec §7: "keep only the ~10
conventions that touch this repo"). Keep those verbatim under a new, shorter
`## Non-negotiable conventions (repo-specific deltas)` heading; replace the rest with:

```markdown
## Non-negotiable conventions

The fleet-wide constitution lives in
[`chrysa/shared-standards/CLAUDE.md`](https://github.com/chrysa/shared-standards/blob/main/CLAUDE.md)
— read it there; it is not duplicated here (v2 redesign, D-0024). The deltas below are
guideline-checker-specific and take precedence where they conflict with the shared standard.
```

followed by the kept repo-specific sub-bullets from this step's first paragraph.

- [ ] **Step 3: Add D-0024 to `DECISIONS.md`**

Append, following the existing ADR format used by `D-0016`/`D-0020`/`D-0023` (read one of them
first to match heading level, "Status/Context/Decision/Consequences" structure, and date
format):

```markdown
## D-0024 — core/workshop/fleet split + auto-derived proven detectors

**Status:** Accepted (2026-08-19)

**Context:** `guideline-checker` had grown four identities in one package — lint engine,
rule-health, an LLM detector-authoring workshop, and gh-backed fleet governance — with no
enforced boundary between them, and a 113K `CLAUDE.md` duplicating the fleet-wide constitution.

**Decision:** Split into `core/` (no LLM/gh deps, installed by default), `workshop/` (LLM
detector authoring, `[workshop]` extra), `fleet/` (gh-backed governance, `[fleet]` extra), with
an enforced one-way dependency rule (`core/` never imports the other two). Replace the hardcoded
phrase table with a heuristic seed-translator (`core/derive/`) feeding a local, ephemeral,
hash-keyed cache — `check` writes only that cache, never the repo tree. Rule-health becomes the
report's headline, not a tile.

**Kill-test:** a detector proven for a given prose hash that would flip to a different verdict
when re-derived from the same prose in CI, with no prose change, falsifies the determinism
claim — treat any such flip as a P1 bug in `core/derive/seed.py`, not an acceptable variance.

**Consequences:** `pip install guideline-checker` (no extras) has zero LLM/gh transitive
dependencies. `central.py`, `push`, `web/central` are removed (breaking, v2.0.0). Archives the
meta-layer ADRs listed below.
```

Mark the ~10 obsolete health/proposer/mechanisms-vs-values meta-layer ADRs (grep `DECISIONS.md`
for ADR headings discussing the workshop/proposer/health philosophy predating this split — read
each candidate's Context section before archiving, do not archive by heading text alone) with a
one-line `**Archived by D-0024** — superseded; see D-0024.` note directly under their `Status`
line, without deleting their content (ADRs are an append-only historical record).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md DECISIONS.md
git commit -m "docs: shrink CLAUDE.md to repo-specific deltas; add D-0024, archive superseded ADRs"
```

---

### Task 13: Final full-suite verification and version bump

**Files:** none new — verification only, plus the version-relevant files the spec's migration
section calls for.

- [ ] **Step 1: Run the complete test suite one more time from a clean state**

```bash
git status  # confirm working tree is clean before this final check
pytest -q --cov=guideline_checker --cov-report=term-missing
```
Expected: PASS, coverage ≥ 85%.

- [ ] **Step 2: Run ruff and mypy (the repo's own standards, dogfooded)**

```bash
ruff check guideline_checker tests
mypy guideline_checker
```
Expected: both clean. Fix any finding introduced by the moves in Tasks 2–10 before proceeding —
do not suppress a new finding with an inline `# noqa`/`# type: ignore` unless Task-specific code
already justified an equivalent suppression elsewhere in this plan.

- [ ] **Step 3: Run `make docker-test` one final time**

Run: `make docker-test`
Expected: PASS.

- [ ] **Step 4: Confirm the v2.0.0 breaking-change signal**

Per spec §7, this redesign is `v2.0.0` (breaking: `central.py`/`push`/`web/central` removed).
Confirm `guideline-checker`'s versioning is `setuptools-scm`-derived from git tags (per
`pyproject.toml`'s comment, "never bumped manually — D-0019") — if so, this task does **not**
edit a version file; the next `git tag v2.0.0` (a release action, outside this plan's scope)
is what performs the bump. Do not tag from inside this task.

- [ ] **Step 5: Final commit if any cleanup remains**

```bash
git add -A
git commit -m "chore: final verification pass for guideline-checker v2 redesign"
```

(Skip if there is nothing to commit — Steps 1–4 are read-only verification in the common case.)
