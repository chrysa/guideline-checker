# Fleet Origin-Side Distribution Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an origin-side, multi-repo distribution-compliance audit to `guideline-checker` so `synthesize` can report (and optionally PR-fix) whether each chrysa repo carries the managed standards artifacts on its `origin/<default>` branch, immune to the stale-clone trap.

**Architecture:** A `GhClient` wraps the `gh` CLI (the single mock seam). A `Scanner` protocol abstracts file access — `LocalScanner` (filesystem, unchanged behaviour) and `OriginScanner` (reads `origin/<default>` via `GhClient`). A new `distribution` check module reads four managed artifacts through the active scanner and emits standard `Violation`s (so every existing reporter works unchanged). A `repos.yml` manifest drives which repos and which checks apply. `synthesize --source origin` iterates the manifest; an opt-in `--fix` opens one PR per repo and never merges.

**Tech Stack:** Python 3.14, stdlib `subprocess`/`shutil`/`base64`, `pyyaml` (already a dep), argparse CLI, pytest + pytest-mock.

## Global Constraints

- **Python**: `requires-python >=3.14`; `from __future__ import annotations` at the top of every new module.
- **Lint**: ruff 0 warnings. **Types**: mypy strict, 0 errors. **Coverage**: `--cov-fail-under=85` (repo gate).
- **Max function lines 50 · max file lines 500 · cyclomatic complexity <= 10.**
- **Commits**: Conventional Commits (`feat`, `test`, `docs`, `refactor`).
- **No `shell=True`** in any subprocess call. Resolve the binary with `shutil.which("gh")`; pass an argument list.
- **No hardcoded secrets/tokens.** `gh` carries auth; never read a token in code.
- **Test command** (host-forbidden — use Docker/make): `make docker-test`. Single-test form referenced in steps: `python -m pytest tests/<file>::<test> -v` (run inside the test container).
- **Owner default**: GitHub owner is `chrysa` (configurable, default `"chrysa"`).
- **ADR gate**: the pre-commit "ADR gate" hook requires `DECISIONS.md` to change when architecture changes. Task 7 records the ADR; new-module commits in earlier tasks are additive surface, but if the gate trips on an earlier commit, stage the Task 7 ADR entry alongside it.

---

### Task 1: `GhClient` — the gh CLI seam

**Files:**
- Create: `guideline_checker/gh_client.py`
- Test: `tests/test_gh_client.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `GhResult = namedtuple` is NOT used; instead a dataclass `GhResult(ok: bool, stdout: str, stderr: str, code: int)`.
  - `GhRunner = Callable[[Sequence[str]], GhResult]` — the injectable seam (args are the tokens after `gh`).
  - `class GhClient`:
    - `__init__(self, runner: GhRunner | None = None) -> None` (None → real `gh` subprocess runner).
    - `read_file(self, owner: str, repo: str, path: str, ref: str) -> str | None` — raw file text at ref, or `None` if absent (404).
    - `default_branch(self, owner: str, repo: str) -> str` — `.default_branch` from the repo API.
    - `available(self) -> bool` — True if the `gh` binary resolves.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gh_client.py
from __future__ import annotations

from collections.abc import Sequence

from guideline_checker.gh_client import GhClient, GhResult


def _runner(responses: dict[str, GhResult]):
    """Fake runner keyed by the joined argument string."""
    def run(args: Sequence[str]) -> GhResult:
        return responses[" ".join(args)]
    return run


class TestGhClientReadFile:
    def test_returns_raw_text_on_success(self) -> None:
        args = "api -H Accept: application/vnd.github.raw repos/chrysa/foo/contents/LICENSE?ref=main"
        client = GhClient(runner=_runner({args: GhResult(True, "MIT License\n", "", 0)}))
        assert client.read_file("chrysa", "foo", "LICENSE", "main") == "MIT License\n"

    def test_returns_none_on_404(self) -> None:
        args = "api -H Accept: application/vnd.github.raw repos/chrysa/foo/contents/LICENSE?ref=main"
        client = GhClient(runner=_runner({args: GhResult(False, "", "gh: Not Found (HTTP 404)", 1)}))
        assert client.read_file("chrysa", "foo", "LICENSE", "main") is None


class TestGhClientDefaultBranch:
    def test_reads_default_branch(self) -> None:
        args = "api repos/chrysa/foo --jq .default_branch"
        client = GhClient(runner=_runner({args: GhResult(True, "develop\n", "", 0)}))
        assert client.default_branch("chrysa", "foo") == "develop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gh_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guideline_checker.gh_client'`.

- [ ] **Step 3: Write minimal implementation**

```python
# guideline_checker/gh_client.py
"""Thin wrapper over the ``gh`` CLI — the single mock seam for origin-side reads/writes.

All GitHub access funnels through :class:`GhClient`. Tests inject a fake ``runner``;
production uses the real ``gh`` subprocess. No token is ever read in code — ``gh`` owns auth.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

# Raw-content Accept header → the contents API returns file bytes (not base64 JSON).
_RAW_ACCEPT = "Accept: application/vnd.github.raw"


@dataclass(frozen=True)
class GhResult:
    ok: bool
    stdout: str
    stderr: str
    code: int


GhRunner = Callable[[Sequence[str]], GhResult]


def _real_runner(args: Sequence[str]) -> GhResult:
    """Run ``gh <args>`` with a hard timeout; never raises on non-zero exit."""
    gh = shutil.which("gh")
    if gh is None:
        return GhResult(ok=False, stdout="", stderr="gh not found", code=127)
    try:
        proc = subprocess.run(  # noqa: S603 — fixed binary, list args, no shell
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GhResult(ok=False, stdout="", stderr=str(exc), code=1)
    return GhResult(ok=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr, code=proc.returncode)


class GhClient:
    def __init__(self, runner: GhRunner | None = None) -> None:
        self._run: GhRunner = runner or _real_runner

    def available(self) -> bool:
        return shutil.which("gh") is not None

    def read_file(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        result = self._run(["api", "-H", _RAW_ACCEPT, f"repos/{owner}/{repo}/contents/{path}?ref={ref}"])
        return result.stdout if result.ok else None

    def default_branch(self, owner: str, repo: str) -> str:
        result = self._run(["api", f"repos/{owner}/{repo}", "--jq", ".default_branch"])
        return result.stdout.strip() if result.ok else "main"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gh_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/gh_client.py tests/test_gh_client.py
git commit -m "feat: add GhClient wrapper over gh CLI (origin-read seam)"
```

---

### Task 2: `Scanner` protocol + Local/Origin scanners

**Files:**
- Create: `guideline_checker/scanner_source.py`
- Test: `tests/test_scanner_source.py`

**Interfaces:**
- Consumes: `GhClient` (Task 1).
- Produces:
  - `class Scanner(Protocol)`: `read_file(self, rel_path: str) -> str | None` (returns file text relative to the repo root, or `None` if absent).
  - `class LocalScanner`: `__init__(self, root: Path)`; reads `root / rel_path` from the filesystem.
  - `class OriginScanner`: `__init__(self, owner: str, repo: str, client: GhClient, ref: str | None = None)`; if `ref` is None, resolves it once via `client.default_branch(...)` and caches it on `self.ref`; `read_file` delegates to `client.read_file(owner, repo, rel_path, self.ref)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scanner_source.py
from __future__ import annotations

from pathlib import Path

from guideline_checker.gh_client import GhClient, GhResult
from guideline_checker.scanner_source import LocalScanner, OriginScanner


class TestLocalScanner:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
        assert LocalScanner(tmp_path).read_file("LICENSE") == "MIT"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert LocalScanner(tmp_path).read_file("nope.txt") is None


class TestOriginScanner:
    def test_resolves_default_branch_then_reads(self) -> None:
        calls: list[list[str]] = []

        def runner(args):  # type: ignore[no-untyped-def]
            calls.append(list(args))
            if args[1] == "repos/chrysa/foo":
                return GhResult(True, "develop\n", "", 0)
            return GhResult(True, "content-on-develop", "", 0)

        scanner = OriginScanner("chrysa", "foo", GhClient(runner=runner))
        assert scanner.read_file(".chrysa/STANDARDS.md") == "content-on-develop"
        assert scanner.ref == "develop"
        # default branch resolved exactly once even across multiple reads
        scanner.read_file("CLAUDE.md")
        assert sum(1 for c in calls if c[1] == "repos/chrysa/foo") == 1

    def test_explicit_ref_skips_default_branch_lookup(self) -> None:
        def runner(args):  # type: ignore[no-untyped-def]
            assert args[1] != "repos/chrysa/foo", "must not look up default branch"
            return GhResult(True, "x", "", 0)

        scanner = OriginScanner("chrysa", "foo", GhClient(runner=runner), ref="main")
        assert scanner.read_file("LICENSE") == "x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scanner_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guideline_checker.scanner_source'`.

- [ ] **Step 3: Write minimal implementation**

```python
# guideline_checker/scanner_source.py
"""File-access abstraction shared by the distribution audit.

``LocalScanner`` reads the working tree; ``OriginScanner`` reads ``origin/<default>``
via the ``gh`` API and is therefore immune to the stale-clone trap by construction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from guideline_checker.gh_client import GhClient


class Scanner(Protocol):
    def read_file(self, rel_path: str) -> str | None: ...


class LocalScanner:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read_file(self, rel_path: str) -> str | None:
        try:
            return (self.root / rel_path).read_text(encoding="utf-8")
        except OSError:
            return None


class OriginScanner:
    def __init__(self, owner: str, repo: str, client: GhClient, ref: str | None = None) -> None:
        self.owner = owner
        self.repo = repo
        self._client = client
        self._ref = ref

    @property
    def ref(self) -> str:
        if self._ref is None:
            self._ref = self._client.default_branch(self.owner, self.repo)
        return self._ref

    def read_file(self, rel_path: str) -> str | None:
        return self._client.read_file(self.owner, self.repo, rel_path, self.ref)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scanner_source.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/scanner_source.py tests/test_scanner_source.py
git commit -m "feat: add Scanner protocol with Local and Origin scanners"
```

---

### Task 3: Fleet manifest loader (`repos.yml`)

**Files:**
- Create: `guideline_checker/manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: nothing (stdlib + `yaml`).
- Produces:
  - `@dataclass(frozen=True) RepoTarget`: `name: str`, `owner: str = "chrysa"`, `license_applicable: bool = True`, `standards_applicable: bool = True`, `precommit_applicable: bool = True`.
  - `load_manifest(path: Path, owner: str = "chrysa") -> list[RepoTarget]` — parse `repos.yml`, keep only `status == "dev"`. Applicability comes from an optional per-repo `distribution:` mapping (opt-out keys `license: false`, `standards: false`, `precommit: false`); absent keys default to applicable. The legacy `public`/`runtime` fields are NOT consulted (different semantics — see ADR). Malformed/absent file raises `FileNotFoundError`/`ValueError` with a clear message.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manifest.py
from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.manifest import RepoTarget, load_manifest

_YAML = """
repos:
  - name: alpha
    status: dev
  - name: bravo
    status: non-dev
  - name: charlie
    status: dev
    distribution:
      license: false
      precommit: false
"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "repos.yml"
    p.write_text(_YAML, encoding="utf-8")
    return p


class TestLoadManifest:
    def test_keeps_only_dev_repos(self, tmp_path: Path) -> None:
        targets = load_manifest(_write(tmp_path))
        assert [t.name for t in targets] == ["alpha", "charlie"]

    def test_defaults_all_checks_applicable(self, tmp_path: Path) -> None:
        alpha = next(t for t in load_manifest(_write(tmp_path)) if t.name == "alpha")
        assert alpha == RepoTarget(name="alpha", owner="chrysa")
        assert alpha.license_applicable and alpha.standards_applicable and alpha.precommit_applicable

    def test_distribution_opt_out_flags(self, tmp_path: Path) -> None:
        charlie = next(t for t in load_manifest(_write(tmp_path)) if t.name == "charlie")
        assert charlie.license_applicable is False
        assert charlie.precommit_applicable is False
        assert charlie.standards_applicable is True

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "absent.yml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guideline_checker.manifest'`.

- [ ] **Step 3: Write minimal implementation**

```python
# guideline_checker/manifest.py
"""Load the chrysa fleet manifest (``repos.yml``) into audit targets.

Only ``status: dev`` repos are audited. Per-repo applicability is declared in an
optional ``distribution:`` mapping (opt-out: ``license/standards/precommit: false``);
absent keys default to applicable. Legacy ``public``/``runtime`` are intentionally
not reused (their semantics differ — see DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RepoTarget:
    name: str
    owner: str = "chrysa"
    license_applicable: bool = True
    standards_applicable: bool = True
    precommit_applicable: bool = True


def _flag(dist: dict[str, object], key: str) -> bool:
    value = dist.get(key, True)
    return value is not False


def load_manifest(path: Path, owner: str = "chrysa") -> list[RepoTarget]:
    text = path.read_text(encoding="utf-8")  # raises FileNotFoundError when absent
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise ValueError(f"Malformed manifest (expected a top-level 'repos' list): {path}")
    targets: list[RepoTarget] = []
    for entry in data["repos"]:
        if not isinstance(entry, dict) or entry.get("status") != "dev":
            continue
        dist = entry.get("distribution") or {}
        if not isinstance(dist, dict):
            dist = {}
        targets.append(
            RepoTarget(
                name=str(entry["name"]),
                owner=owner,
                license_applicable=_flag(dist, "license"),
                standards_applicable=_flag(dist, "standards"),
                precommit_applicable=_flag(dist, "precommit"),
            )
        )
    return targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/manifest.py tests/test_manifest.py
git commit -m "feat: add repos.yml fleet manifest loader with declarative applicability"
```

---

### Task 4: Distribution expectations + check registry

**Files:**
- Create: `guideline_checker/distribution.py`
- Test: `tests/test_distribution.py`

**Interfaces:**
- Consumes: `Scanner` (Task 2), `RepoTarget` (Task 3), `Violation` from `guideline_checker.checker`.
- Produces:
  - `@dataclass(frozen=True) Expectations`: `canonical_standards: str`, `license_text: str`, `precommit_repo: str = "chrysa/pre-commit-tools"`, `import_marker: str = "@.chrysa/STANDARDS.md"`.
  - Path constants: `STANDARDS_PATH = ".chrysa/STANDARDS.md"`, `CLAUDE_PATH = "CLAUDE.md"`, `PRECOMMIT_PATH = ".pre-commit-config.yaml"`, `LICENSE_PATH = "LICENSE"`.
  - `load_expectations(shared_standards_root: Path) -> Expectations` — reads canonical `standards/STANDARDS.chrysa.md` and `templates/LICENSE.mit` from a `shared-standards` checkout.
  - `audit(scanner: Scanner, target: RepoTarget, expected: Expectations) -> list[Violation]` — runs each applicable check, returns `Violation`s. Each `Violation.file` is `Path(<rel artifact path>)`, `rule` is the check id, `line_number=1`.
  - Module-level `CHECK_IDS: tuple[str, ...] = ("standards-file", "claude-import", "precommit-pin", "license-present")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_distribution.py
from __future__ import annotations

from pathlib import Path

from guideline_checker.distribution import Expectations, audit
from guideline_checker.manifest import RepoTarget

_CANON = "# chrysa — Transverse Standards\nbody\n"
_EXP = Expectations(canonical_standards=_CANON, license_text="MIT License\n")


class _FakeScanner:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def read_file(self, rel_path: str) -> str | None:
        return self._files.get(rel_path)


def _compliant_files() -> dict[str, str]:
    return {
        ".chrysa/STANDARDS.md": _CANON,
        "CLAUDE.md": "# Repo\n@.chrysa/STANDARDS.md\n",
        ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/chrysa/pre-commit-tools\n",
        "LICENSE": "MIT License\n",
    }


class TestAuditCompliant:
    def test_no_violations_when_all_present(self) -> None:
        target = RepoTarget(name="alpha")
        assert audit(_FakeScanner(_compliant_files()), target, _EXP) == []


class TestAuditDrift:
    def test_standards_file_mismatch(self) -> None:
        files = _compliant_files()
        files[".chrysa/STANDARDS.md"] = "stale\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["standards-file"]
        assert str(violations[0].file) == ".chrysa/STANDARDS.md"

    def test_missing_claude_import(self) -> None:
        files = _compliant_files()
        files["CLAUDE.md"] = "# Repo\nno import here\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["claude-import"]

    def test_missing_precommit_pin(self) -> None:
        files = _compliant_files()
        files[".pre-commit-config.yaml"] = "repos: []\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["precommit-pin"]

    def test_missing_license(self) -> None:
        files = _compliant_files()
        del files["LICENSE"]
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["license-present"]


class TestApplicability:
    def test_non_applicable_license_is_not_a_violation(self) -> None:
        files = _compliant_files()
        del files["LICENSE"]
        target = RepoTarget(name="perso", license_applicable=False)
        assert audit(_FakeScanner(files), target, _EXP) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_distribution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guideline_checker.distribution'`.

- [ ] **Step 3: Write minimal implementation**

```python
# guideline_checker/distribution.py
"""Origin-side distribution-compliance checks.

File presence/equality checks (not per-line regex) emitted as standard ``Violation``s,
so every existing reporter and the web dashboard render them unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from guideline_checker.checker import Violation
from guideline_checker.manifest import RepoTarget
from guideline_checker.scanner_source import Scanner

STANDARDS_PATH = ".chrysa/STANDARDS.md"
CLAUDE_PATH = "CLAUDE.md"
PRECOMMIT_PATH = ".pre-commit-config.yaml"
LICENSE_PATH = "LICENSE"

CHECK_IDS: tuple[str, ...] = ("standards-file", "claude-import", "precommit-pin", "license-present")


@dataclass(frozen=True)
class Expectations:
    canonical_standards: str
    license_text: str
    precommit_repo: str = "chrysa/pre-commit-tools"
    import_marker: str = "@.chrysa/STANDARDS.md"


def load_expectations(shared_standards_root: Path) -> Expectations:
    canonical = (shared_standards_root / "standards" / "STANDARDS.chrysa.md").read_text(encoding="utf-8")
    license_text = (shared_standards_root / "templates" / "LICENSE.mit").read_text(encoding="utf-8")
    return Expectations(canonical_standards=canonical, license_text=license_text)


def _violation(rel_path: str, check_id: str, message: str) -> Violation:
    return Violation(file=Path(rel_path), line_number=1, line_content=message, rule=check_id, severity="error")


def _check_standards(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(STANDARDS_PATH)
    if content == exp.canonical_standards:
        return None
    msg = "missing" if content is None else "differs from canonical STANDARDS.chrysa.md"
    return _violation(STANDARDS_PATH, "standards-file", f".chrysa/STANDARDS.md {msg}")


def _check_claude_import(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(CLAUDE_PATH)
    if content is not None and exp.import_marker in content:
        return None
    return _violation(CLAUDE_PATH, "claude-import", f"CLAUDE.md missing '{exp.import_marker}' import")


def _check_precommit(scanner: Scanner, exp: Expectations) -> Violation | None:
    content = scanner.read_file(PRECOMMIT_PATH)
    if content is not None and exp.precommit_repo in content:
        return None
    return _violation(PRECOMMIT_PATH, "precommit-pin", f"pre-commit missing {exp.precommit_repo} pin")


def _check_license(scanner: Scanner, _exp: Expectations) -> Violation | None:
    if scanner.read_file(LICENSE_PATH) is not None:
        return None
    return _violation(LICENSE_PATH, "license-present", "LICENSE absent")


def audit(scanner: Scanner, target: RepoTarget, expected: Expectations) -> list[Violation]:
    violations: list[Violation] = []
    if target.standards_applicable:
        violations.append(_check_standards(scanner, expected))
        violations.append(_check_claude_import(scanner, expected))
    if target.precommit_applicable:
        violations.append(_check_precommit(scanner, expected))
    if target.license_applicable:
        violations.append(_check_license(scanner, expected))
    return [v for v in violations if v is not None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_distribution.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add guideline_checker/distribution.py tests/test_distribution.py
git commit -m "feat: add distribution-compliance check registry emitting Violations"
```

---

### Task 5: Origin-mode audit driver (manifest → RuleResult entries)

**Files:**
- Create: `guideline_checker/origin_audit.py`
- Test: `tests/test_origin_audit.py`

**Interfaces:**
- Consumes: `GhClient` (T1), `OriginScanner` (T2), `RepoTarget`/`load_manifest` (T3), `audit`/`Expectations`/`load_expectations` (T4), `RuleResult`/`InstructionFile`/`SourceType` from checker/loader.
- Produces:
  - `@dataclass DistRepoResult`: `name: str`, `results: list[RuleResult]`, `errors: int`, `warnings: int`, `fetch_failed: bool`.
  - `run_origin_audit(targets: list[RepoTarget], expected: Expectations, client: GhClient) -> list[DistRepoResult]` — for each target builds an `OriginScanner`, calls `distribution.audit`, wraps the `Violation`s in a single synthetic `RuleResult` whose `instruction` is an `InstructionFile(path=Path("<distribution>"), apply_to="", description="distribution compliance", content="", source_type=SourceType.GUIDELINES_YAML, rules=list(CHECK_IDS))`. A fetch failure (scanner returns `None` for *every* artifact while `gh` reports an error — detect via `client.read_file` returning None on a probe of the repo root) yields one `Violation(rule="origin-fetch-failed", severity="error")` and `fetch_failed=True`.

> Note on fetch-failure detection: a repo where every artifact is legitimately absent is indistinguishable from an auth failure by file reads alone. Use `client.default_branch(...)` as the probe: the real runner returns `"main"` only on success; on failure the `GhClient.default_branch` falls back to `"main"` too — so instead add a `GhClient.repo_exists(owner, repo) -> bool` (`gh api repos/{owner}/{repo} --jq .name`, ok flag) and call it once per repo before auditing. This step includes adding that method.

- [ ] **Step 1: Add `repo_exists` to GhClient (failing test first)**

```python
# tests/test_gh_client.py  (append)
class TestGhClientRepoExists:
    def test_true_when_repo_resolves(self) -> None:
        args = "api repos/chrysa/foo --jq .name"
        client = GhClient(runner=_runner({args: GhResult(True, "foo\n", "", 0)}))
        assert client.repo_exists("chrysa", "foo") is True

    def test_false_on_error(self) -> None:
        args = "api repos/chrysa/foo --jq .name"
        client = GhClient(runner=_runner({args: GhResult(False, "", "404", 1)}))
        assert client.repo_exists("chrysa", "foo") is False
```

Run: `python -m pytest tests/test_gh_client.py::TestGhClientRepoExists -v` → FAIL (`AttributeError: 'GhClient' object has no attribute 'repo_exists'`).

- [ ] **Step 2: Implement `repo_exists`**

```python
# guideline_checker/gh_client.py  (add method to GhClient)
    def repo_exists(self, owner: str, repo: str) -> bool:
        return self._run(["api", f"repos/{owner}/{repo}", "--jq", ".name"]).ok
```

Run: `python -m pytest tests/test_gh_client.py -v` → PASS.

- [ ] **Step 3: Write the failing driver test**

```python
# tests/test_origin_audit.py
from __future__ import annotations

from guideline_checker.distribution import Expectations
from guideline_checker.gh_client import GhClient, GhResult
from guideline_checker.manifest import RepoTarget
from guideline_checker.origin_audit import run_origin_audit

_CANON = "# chrysa — Transverse Standards\nbody\n"
_EXP = Expectations(canonical_standards=_CANON, license_text="MIT License\n")


def _make_runner(repo_files: dict[str, dict[str, str]]):
    """Runner serving per-repo file maps; unknown repo → 404."""
    def runner(args):  # type: ignore[no-untyped-def]
        joined = " ".join(args)
        # repo_exists probe
        if joined.endswith("--jq .name"):
            repo = args[1].split("/")[-1]
            return GhResult(repo in repo_files, repo + "\n", "", 0)
        if joined.endswith("--jq .default_branch"):
            return GhResult(True, "main\n", "", 0)
        # contents read: repos/chrysa/<repo>/contents/<path>?ref=main
        spec = args[-1]  # repos/chrysa/<repo>/contents/<path>?ref=main
        repo = spec.split("/")[2]
        path = spec.split("/contents/")[1].split("?")[0]
        files = repo_files.get(repo, {})
        return (GhResult(True, files[path], "", 0) if path in files else GhResult(False, "", "404", 1))
    return runner


def _compliant() -> dict[str, str]:
    return {
        ".chrysa/STANDARDS.md": _CANON,
        "CLAUDE.md": "@.chrysa/STANDARDS.md\n",
        ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/chrysa/pre-commit-tools\n",
        "LICENSE": "MIT License\n",
    }


class TestRunOriginAudit:
    def test_compliant_repo_has_no_violations(self) -> None:
        client = GhClient(runner=_make_runner({"alpha": _compliant()}))
        results = run_origin_audit([RepoTarget(name="alpha")], _EXP, client)
        assert results[0].errors == 0
        assert results[0].fetch_failed is False

    def test_drifting_repo_reports_errors(self) -> None:
        files = _compliant()
        del files["LICENSE"]
        client = GhClient(runner=_make_runner({"alpha": files}))
        results = run_origin_audit([RepoTarget(name="alpha")], _EXP, client)
        assert results[0].errors == 1

    def test_unreachable_repo_marks_fetch_failed(self) -> None:
        client = GhClient(runner=_make_runner({}))  # no repos resolve
        results = run_origin_audit([RepoTarget(name="ghost")], _EXP, client)
        assert results[0].fetch_failed is True
        assert results[0].errors == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_origin_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guideline_checker.origin_audit'`.

- [ ] **Step 5: Write minimal implementation**

```python
# guideline_checker/origin_audit.py
"""Drive the distribution audit across a fleet manifest, origin-side.

Wraps each repo's distribution violations in a synthetic ``RuleResult`` so the
existing synthesis reporter consumes origin findings with no reporter changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from guideline_checker.checker import RuleResult, Violation
from guideline_checker.distribution import CHECK_IDS, Expectations, audit
from guideline_checker.gh_client import GhClient
from guideline_checker.loader import InstructionFile, SourceType
from guideline_checker.manifest import RepoTarget
from guideline_checker.scanner_source import OriginScanner


@dataclass
class DistRepoResult:
    name: str
    results: list[RuleResult] = field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    fetch_failed: bool = False


def _synthetic_instruction() -> InstructionFile:
    return InstructionFile(
        path=Path("<distribution>"),
        apply_to="",
        description="distribution compliance",
        content="",
        source_type=SourceType.GUIDELINES_YAML,
        rules=list(CHECK_IDS),
    )


def _wrap(violations: list[Violation]) -> DistRepoResult:
    result = RuleResult(instruction=_synthetic_instruction(), violations=violations, files_checked=len(CHECK_IDS))
    errors = sum(1 for v in violations if v.severity == "error")
    warnings = sum(1 for v in violations if v.severity == "warning")
    return DistRepoResult(name="", results=[result], errors=errors, warnings=warnings)


def run_origin_audit(targets: list[RepoTarget], expected: Expectations, client: GhClient) -> list[DistRepoResult]:
    out: list[DistRepoResult] = []
    for target in targets:
        if not client.repo_exists(target.owner, target.name):
            failure = Violation(
                file=Path("<origin>"),
                line_number=1,
                line_content=f"cannot reach origin for {target.owner}/{target.name}",
                rule="origin-fetch-failed",
                severity="error",
            )
            wrapped = _wrap([failure])
            wrapped.name, wrapped.fetch_failed = target.name, True
            out.append(wrapped)
            continue
        scanner = OriginScanner(target.owner, target.name, client)
        wrapped = _wrap(audit(scanner, target, expected))
        wrapped.name = target.name
        out.append(wrapped)
    return out
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_origin_audit.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add guideline_checker/origin_audit.py guideline_checker/gh_client.py tests/test_origin_audit.py tests/test_gh_client.py
git commit -m "feat: drive distribution audit across fleet manifest origin-side"
```

---

### Task 6: CLI wiring — `synthesize --source origin`

**Files:**
- Modify: `guideline_checker/cli.py` (synthesize parser at `142-187`; `_cmd_synthesize` at `469-556`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `load_manifest` (T3), `load_expectations` (T4), `run_origin_audit` (T5), `GhClient` (T1), `SynthesisHtmlReporter`.
- Produces: new synthesize flags `--source {local,origin}` (default `local`), `--manifest PATH`, `--shared-standards PATH`, `--category {all,distribution}` (default `all`). A new function `_cmd_synthesize_origin(args) -> int` building `repo_entries` dicts shaped exactly like the local path (keys: `name`, `path`, `skipped`, `results`, `linter_results=[]`, `report_path=None`, `errors`, `warnings`) and calling `SynthesisHtmlReporter().write(...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
from unittest.mock import patch

from guideline_checker.gh_client import GhResult


def _origin_runner(args):  # type: ignore[no-untyped-def]
    joined = " ".join(args)
    if joined.endswith("--jq .name"):
        return GhResult(True, "alpha\n", "", 0)
    if joined.endswith("--jq .default_branch"):
        return GhResult(True, "main\n", "", 0)
    return GhResult(False, "", "404", 1)  # all artifacts absent → drift


class TestSynthesizeOrigin:
    def test_origin_source_writes_report_and_returns_zero(self, tmp_path):  # type: ignore[no-untyped-def]
        manifest = tmp_path / "repos.yml"
        manifest.write_text("repos:\n  - name: alpha\n    status: dev\n", encoding="utf-8")
        shared = tmp_path / "shared-standards"
        (shared / "standards").mkdir(parents=True)
        (shared / "templates").mkdir(parents=True)
        (shared / "standards" / "STANDARDS.chrysa.md").write_text("CANON\n", encoding="utf-8")
        (shared / "templates" / "LICENSE.mit").write_text("MIT\n", encoding="utf-8")
        out = tmp_path / "synthesis.html"

        from guideline_checker.cli import main

        with patch("guideline_checker.cli.GhClient") as gh_cls:
            gh_cls.return_value.repo_exists.side_effect = lambda o, r: True
            # delegate read_file/default_branch to a real GhClient over the fake runner
            from guideline_checker.gh_client import GhClient as RealClient
            gh_cls.return_value = RealClient(runner=_origin_runner)
            code = main(
                [
                    "synthesize", "--source", "origin",
                    "--manifest", str(manifest),
                    "--shared-standards", str(shared),
                    "--workspace", str(tmp_path),
                    "--output", str(out),
                ]
            )
        assert code == 0
        assert out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::TestSynthesizeOrigin -v`
Expected: FAIL (`error: unrecognized arguments: --source`).

- [ ] **Step 3: Add the parser flags**

In `guideline_checker/cli.py`, inside the synthesize subparser block (after the `--instructions` argument, before line 189), add:

```python
    syn_cmd.add_argument(
        "--source",
        choices=["local", "origin"],
        default="local",
        help="Audit local working trees (default) or origin/<default-branch> via the gh API.",
    )
    syn_cmd.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to repos.yml. Required when --source origin.",
    )
    syn_cmd.add_argument(
        "--shared-standards",
        type=Path,
        default=None,
        dest="shared_standards",
        help="Path to a shared-standards checkout (canonical STANDARDS + LICENSE template). Required for --source origin.",
    )
    syn_cmd.add_argument(
        "--category",
        choices=["all", "distribution"],
        default="all",
        help="Restrict origin audit to a check category (default: all = distribution).",
    )
```

- [ ] **Step 4: Branch `_cmd_synthesize` to the origin path**

At the top of `_cmd_synthesize` (line 470, before resolving `workspace`), add:

```python
    if getattr(args, "source", "local") == "origin":
        return _cmd_synthesize_origin(args)
```

Then add the new function after `_cmd_synthesize` (after line 556):

```python
def _cmd_synthesize_origin(args: argparse.Namespace) -> int:
    """Audit origin/<default> for every dev repo in the manifest; write a synthesis report."""
    from guideline_checker.distribution import load_expectations
    from guideline_checker.gh_client import GhClient
    from guideline_checker.manifest import load_manifest
    from guideline_checker.origin_audit import run_origin_audit
    from guideline_checker.reporters.synthesis_html import SynthesisHtmlReporter

    if args.manifest is None or args.shared_standards is None:
        print("[guideline-checker] --source origin requires --manifest and --shared-standards", file=sys.stderr)
        return 2
    client = GhClient()
    if not client.available():
        print("[guideline-checker] gh CLI not found — required for --source origin", file=sys.stderr)
        return 2

    targets = load_manifest(args.manifest)
    expected = load_expectations(args.shared_standards)
    print(f"[guideline-checker] Auditing {len(targets)} dev repo(s) on origin ...")
    audited = run_origin_audit(targets, expected, client)

    workspace: Path = args.workspace.resolve()
    output: Path = args.output or workspace / "guideline-synthesis.html"
    repo_entries = [
        {
            "name": r.name,
            "path": workspace / r.name,
            "skipped": False,
            "results": r.results,
            "linter_results": [],
            "report_path": None,
            "errors": r.errors,
            "warnings": r.warnings,
        }
        for r in audited
    ]
    SynthesisHtmlReporter().write(workspace=workspace, repo_entries=repo_entries, output_path=output)
    total_errors = sum(r.errors for r in audited)
    print(f"[guideline-checker] Origin synthesis written to: {output} (errors={total_errors})")
    return 0
```

Add `from guideline_checker.gh_client import GhClient` to the module-level imports (so the test's `patch("guideline_checker.cli.GhClient")` target exists).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py::TestSynthesizeOrigin -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite + lint + types**

Run: `make docker-test`
Expected: all green, coverage ≥ 85%.

- [ ] **Step 7: Commit**

```bash
git add guideline_checker/cli.py tests/test_cli.py
git commit -m "feat: add synthesize --source origin for fleet distribution audit"
```

---

### Task 7: `--fix` remediation (one PR per repo, never merges)

**Files:**
- Create: `guideline_checker/fixers.py`
- Modify: `guideline_checker/gh_client.py` (add write methods), `guideline_checker/cli.py` (synthesize `--fix`/`--dry-run`)
- Modify: `DECISIONS.md` (ADR entry — satisfies the ADR-gate hook)
- Test: `tests/test_fixers.py`, `tests/test_gh_client.py` (append)

**Interfaces:**
- Consumes: `GhClient` (T1), `Expectations`/check ids (T4), `Violation`.
- Produces:
  - `GhClient` write methods: `branch_sha(owner, repo, branch) -> str | None`; `create_branch(owner, repo, new_branch, from_sha) -> bool`; `put_file(owner, repo, path, content, message, branch) -> bool`; `open_pr(owner, repo, head, base, title, body) -> str | None` (returns PR URL or None); `find_pr(owner, repo, head) -> str | None` (idempotency).
  - `fixers.py`: `FIX_CONTENT: dict[str, Callable[[Expectations], str]]` mapping check id → file content producer; `ARTIFACT_PATH: dict[str, str]` (check id → repo-relative path, reusing `distribution` constants); `@dataclass FixPlan(repo: str, paths: list[str], dry_run: bool)`; `plan_fixes(repo_result, expected) -> FixPlan`; `apply_fix(owner, repo_result, expected, client, dry_run) -> str | None` (returns PR URL, `"DRY-RUN"`, or None). Branch name `chore/distribution-fixes`.

- [ ] **Step 1: Write failing tests for the content producers (pure functions first)**

```python
# tests/test_fixers.py
from __future__ import annotations

from pathlib import Path

from guideline_checker.checker import RuleResult, Violation
from guideline_checker.distribution import Expectations
from guideline_checker.fixers import ARTIFACT_PATH, FIX_CONTENT, plan_fixes
from guideline_checker.loader import InstructionFile, SourceType

_EXP = Expectations(canonical_standards="CANON\n", license_text="MIT\n")


def test_license_fixer_returns_template() -> None:
    assert FIX_CONTENT["license-present"](_EXP) == "MIT\n"


def test_standards_fixer_returns_canonical() -> None:
    assert FIX_CONTENT["standards-file"](_EXP) == "CANON\n"


def test_artifact_paths_cover_all_fixers() -> None:
    assert set(ARTIFACT_PATH) == set(FIX_CONTENT)


def _result(rules: list[str]) -> RuleResult:
    instr = InstructionFile(path=Path("<d>"), apply_to="", description="", content="", source_type=SourceType.GUIDELINES_YAML)
    viols = [Violation(file=Path(ARTIFACT_PATH[r]), line_number=1, line_content="", rule=r, severity="error") for r in rules]
    return RuleResult(instruction=instr, violations=viols)


def test_plan_includes_only_fixable_paths() -> None:
    plan = plan_fixes(_result(["license-present", "standards-file"]), _EXP)
    assert sorted(plan.paths) == ["LICENSE", ".chrysa/STANDARDS.md"][::-1] or sorted(plan.paths) == sorted(["LICENSE", ".chrysa/STANDARDS.md"])
```

> The last assertion is intentionally order-independent; keep only the `sorted(...) == sorted([...])` form when implementing — delete the `[::-1]` alternative.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fixers.py -v`
Expected: FAIL (`ModuleNotFoundError: guideline_checker.fixers`).

- [ ] **Step 3: Implement the content producers + planner**

```python
# guideline_checker/fixers.py
"""Remediation producers + PR planner for distribution drift.

Opt-in. Opens ONE PR per repo; never merges. Idempotent: an existing fix branch/PR
short-circuits. ``precommit-pin`` and ``claude-import`` need the current file content
(append/inject), so they are applied only when the file already exists; a wholly
missing pre-commit/CLAUDE file is reported but left for a human (no safe full-file template).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from guideline_checker.checker import RuleResult
from guideline_checker.distribution import (
    LICENSE_PATH,
    STANDARDS_PATH,
    Expectations,
)
from guideline_checker.gh_client import GhClient

_FIX_BRANCH = "chore/distribution-fixes"

# Only checks with a safe whole-file remediation are auto-fixable here.
FIX_CONTENT: dict[str, Callable[[Expectations], str]] = {
    "license-present": lambda exp: exp.license_text,
    "standards-file": lambda exp: exp.canonical_standards,
}
ARTIFACT_PATH: dict[str, str] = {
    "license-present": LICENSE_PATH,
    "standards-file": STANDARDS_PATH,
}


@dataclass
class FixPlan:
    repo: str
    paths: list[str]
    dry_run: bool


def plan_fixes(repo_result: RuleResult, _expected: Expectations) -> FixPlan:
    paths = [ARTIFACT_PATH[v.rule] for v in repo_result.violations if v.rule in FIX_CONTENT]
    return FixPlan(repo="", paths=paths, dry_run=False)


def apply_fix(
    owner: str,
    repo: str,
    repo_result: RuleResult,
    expected: Expectations,
    client: GhClient,
    dry_run: bool,
) -> str | None:
    fixable = [v for v in repo_result.violations if v.rule in FIX_CONTENT]
    if not fixable:
        return None
    if dry_run:
        return "DRY-RUN"
    existing = client.find_pr(owner, repo, _FIX_BRANCH)
    if existing is not None:
        return existing
    base = client.default_branch(owner, repo)
    sha = client.branch_sha(owner, repo, base)
    if sha is None or not client.create_branch(owner, repo, _FIX_BRANCH, sha):
        return None
    for v in fixable:
        content = FIX_CONTENT[v.rule](expected)
        client.put_file(owner, repo, ARTIFACT_PATH[v.rule], content, f"chore: fix {v.rule} distribution drift", _FIX_BRANCH)
    body = "Automated distribution-drift remediation by guideline-checker.\n\nRefs: standards distribution."
    return client.open_pr(owner, repo, _FIX_BRANCH, base, "chore: fix standards distribution drift", body)
```

- [ ] **Step 4: Run the pure-function tests**

Run: `python -m pytest tests/test_fixers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Add + test GhClient write methods**

```python
# tests/test_gh_client.py  (append)
class TestGhClientWrites:
    def test_branch_sha(self) -> None:
        args = "api repos/chrysa/foo/git/ref/heads/main --jq .object.sha"
        client = GhClient(runner=_runner({args: GhResult(True, "abc123\n", "", 0)}))
        assert client.branch_sha("chrysa", "foo", "main") == "abc123"

    def test_find_pr_returns_url_when_open(self) -> None:
        args = "pr list --repo chrysa/foo --head chore/distribution-fixes --state open --json url --jq .[0].url"
        client = GhClient(runner=_runner({args: GhResult(True, "https://github.com/chrysa/foo/pull/9\n", "", 0)}))
        assert client.find_pr("chrysa", "foo", "chore/distribution-fixes") == "https://github.com/chrysa/foo/pull/9"

    def test_find_pr_returns_none_when_absent(self) -> None:
        args = "pr list --repo chrysa/foo --head chore/distribution-fixes --state open --json url --jq .[0].url"
        client = GhClient(runner=_runner({args: GhResult(True, "\n", "", 0)}))
        assert client.find_pr("chrysa", "foo", "chore/distribution-fixes") is None
```

Run: `python -m pytest tests/test_gh_client.py::TestGhClientWrites -v` → FAIL.

```python
# guideline_checker/gh_client.py  (add methods to GhClient; import base64 at top)
    def branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        r = self._run(["api", f"repos/{owner}/{repo}/git/ref/heads/{branch}", "--jq", ".object.sha"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def create_branch(self, owner: str, repo: str, new_branch: str, from_sha: str) -> bool:
        return self._run(
            ["api", "--method", "POST", f"repos/{owner}/{repo}/git/refs",
             "-f", f"ref=refs/heads/{new_branch}", "-f", f"sha={from_sha}"]
        ).ok

    def put_file(self, owner: str, repo: str, path: str, content: str, message: str, branch: str) -> bool:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        args = ["api", "--method", "PUT", f"repos/{owner}/{repo}/contents/{path}",
                "-f", f"message={message}", "-f", f"content={encoded}", "-f", f"branch={branch}"]
        existing_sha = self._content_sha(owner, repo, path, branch)
        if existing_sha is not None:
            args += ["-f", f"sha={existing_sha}"]
        return self._run(args).ok

    def _content_sha(self, owner: str, repo: str, path: str, ref: str) -> str | None:
        r = self._run(["api", f"repos/{owner}/{repo}/contents/{path}?ref={ref}", "--jq", ".sha"])
        return r.stdout.strip() if r.ok and r.stdout.strip() else None

    def open_pr(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> str | None:
        r = self._run(["pr", "create", "--repo", f"{owner}/{repo}", "--head", head,
                        "--base", base, "--title", title, "--body", body])
        return r.stdout.strip() if r.ok else None

    def find_pr(self, owner: str, repo: str, head: str) -> str | None:
        r = self._run(["pr", "list", "--repo", f"{owner}/{repo}", "--head", head,
                       "--state", "open", "--json", "url", "--jq", ".[0].url"])
        url = r.stdout.strip()
        return url if (r.ok and url) else None
```

Run: `python -m pytest tests/test_gh_client.py -v` → PASS.

- [ ] **Step 6: Wire `--fix`/`--dry-run` into the synthesize parser and origin command**

In the synthesize subparser block add:

```python
    syn_cmd.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Open one PR per repo to remediate fixable distribution drift (origin source only). Never merges.",
    )
    syn_cmd.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="With --fix: print the PRs that would be opened without creating them.",
    )
```

In `_cmd_synthesize_origin`, after `audited = run_origin_audit(...)` and before building `repo_entries`, add:

```python
    if getattr(args, "fix", False):
        from guideline_checker.fixers import apply_fix

        for r in audited:
            if r.fetch_failed:
                continue
            url = apply_fix("chrysa", r.name, r.results[0], expected, client, args.dry_run)
            if url == "DRY-RUN":
                print(f"[guideline-checker]   {r.name}: would open a distribution-fix PR")
            elif url:
                print(f"[guideline-checker]   {r.name}: PR {url}")
```

- [ ] **Step 7: Add the ADR entry (satisfies ADR-gate hook)**

Append to `DECISIONS.md` a new ADR: `ADR-GC-0NN: origin-side distribution audit`. Record: the `Scanner` abstraction + `GhClient` seam; distribution checks emitted as `Violation`s (reuse reporters); `repos.yml` `distribution:` opt-out flags (NOT overloading legacy `public`/`runtime`); `--fix` opens one PR per repo and never merges; only `license-present` + `standards-file` are auto-fixable (whole-file safe), `precommit-pin`/`claude-import` are report-only because they require content-aware edits.

- [ ] **Step 8: Full gate**

Run: `make docker-test`
Expected: all green, coverage ≥ 85%.

- [ ] **Step 9: Commit**

```bash
git add guideline_checker/fixers.py guideline_checker/gh_client.py guideline_checker/cli.py DECISIONS.md tests/test_fixers.py tests/test_gh_client.py
git commit -m "feat: add opt-in --fix that PRs distribution remediations (never merges)"
```

---

### Task 8: Docs — README usage + manifest example

**Files:**
- Modify: `README.md` (synthesize section)
- Modify: `guidelines/` example or `repos.yml` doc snippet (documentation only; the real `repos.yml` lives in shared-standards)

**Interfaces:** none (docs only).

- [ ] **Step 1: Document the origin audit in README**

Add a "Fleet distribution audit (origin-side)" subsection under the synthesize docs, with the exact command:

```bash
guideline-checker synthesize \
  --source origin \
  --manifest ../shared-standards/repos.yml \
  --shared-standards ../shared-standards \
  --workspace . \
  --category distribution \
  --output fleet-distribution.html
# add --fix [--dry-run] to open one remediation PR per drifting repo (never merges)
```

Document the four checks (`standards-file`, `claude-import`, `precommit-pin`, `license-present`), the `origin-fetch-failed` error finding, and the `repos.yml` `distribution:` opt-out block:

```yaml
  - name: my-resume
    status: dev
    distribution:
      license: false      # personal-content repo — no blanket MIT
```

- [ ] **Step 2: Verify docs build/lint (markdown) and full suite**

Run: `make docker-test`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document origin-side fleet distribution audit and --fix"
```

---

## Self-Review

**1. Spec coverage**
- Scanner protocol (Local/Origin) → Task 2 (+ GhClient seam Task 1). ✓
- Distribution check category, 4 checks emitted as `Violation` → Task 4. ✓
- Tri-state (OK/DRIFT/NA/ERROR): OK=no violation, DRIFT=Violation, NA=applicability skip (Task 4 `TestApplicability`), ERROR=`origin-fetch-failed` (Task 5). ✓
- Manifest + declarative applicability, backward-compatible → Task 3 (opt-out `distribution:` block; absent = applicable). ✓
- CLI `--source/--manifest/--category` → Task 6; `--fix/--dry-run` → Task 7. ✓
- `--fix` one PR per repo, never merges, idempotent, dry-run → Task 7. ✓
- Tests via the gh seam, pytest+mock, mypy/ruff/coverage → every task. ✓
- Reports/dashboard unchanged → guaranteed by emitting `Violation`/`RuleResult` (Task 5 synthetic instruction); no reporter touched. ✓
- **Spec deviations (deliberate, documented in ADR Task 7):** (a) the per-line rule engine is NOT rewired to read origin — the Scanner protocol is introduced and used by the distribution category only; full per-line-over-origin is future work (the spec called the Scanner "the keystone…for all reporting", but wiring `run_checks`' ProcessPool/rglob/Path internals to origin is a separate, larger change). (b) Applicability uses a new `distribution:` block instead of the spec's `personal: true`, to avoid overloading the existing `public`/`runtime` semantics already in repos.yml. (c) Only `license-present` + `standards-file` are auto-fixable; `precommit-pin`/`claude-import` are report-only (no safe whole-file template — they need content-aware edits). All three are noted here and in the ADR.

**2. Placeholder scan:** No TBD/TODO. The one order-dependent assertion in Task 7 Step 1 carries an explicit instruction to keep only the `sorted(...)==sorted(...)` form. ✓

**3. Type consistency:** `read_file(rel_path)->str|None` used identically by LocalScanner/OriginScanner/FakeScanner. `Violation(file=Path, line_number, line_content, rule, severity)` matches `checker.py:155`. `RuleResult(instruction, violations, files_checked)` matches `checker.py:164`. `InstructionFile(path, apply_to, description, content, source_type, rules)` matches `loader.py:47`. `GhClient` method names (`read_file/default_branch/repo_exists/branch_sha/create_branch/put_file/open_pr/find_pr`) are consistent across Tasks 1/5/7. `RepoTarget` field names (`*_applicable`) consistent across Tasks 3/4. ✓
