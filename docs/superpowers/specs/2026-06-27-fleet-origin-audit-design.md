# Fleet origin-side distribution audit — design

Date: 2026-06-27
Repo: chrysa/guideline-checker
Status: approved design (pending implementation plan)

## Problem

The chrysa fleet has a verification tool (`guideline-checker`) that scans **local
source trees** for rule violations and rolls them up across a workspace via the
`synthesize` command. Two gaps motivate this work:

1. **No structural/distribution audit.** Nothing verifies that each repo actually
   *carries* the managed standards artifacts on its default branch — that
   `.chrysa/STANDARDS.md` matches the canonical source, that `CLAUDE.md` imports it,
   that the `chrysa/pre-commit-tools` pin is present, that a `LICENSE` exists.
   `distribute-standards.sh` can `--check` this but only against a **local checkout**,
   one repo at a time.

2. **The stale-clone trap.** Every multi-repo audit in this ecosystem that reads local
   checkouts is unreliable: local clones routinely lag `origin` by 5–18 commits and sit
   on stale feature branches. `guideline-checker synthesize` enumerates local workspace
   subdirectories, so it inherits this trap — a repo can look non-compliant locally
   while `origin/<default>` is perfectly fine (and vice-versa). This caused real wasted
   churn (redundant standards redistribution and pre-commit merges later reverted).

## Goal

Add an **origin-side, multi-repo distribution-compliance audit** to `guideline-checker`,
and in doing so make its multi-repo reporting **truthful to `origin/<default>`** rather
than to whatever happens to be checked out locally. Optionally remediate drift by
opening PRs (never auto-merging).

## Non-goals / YAGNI

- No auto-merge. `--fix` opens PRs; merging stays a separate, deliberate human action.
- No nuanced content checks (Python version, `requires-python`, Dockerfile base, src
  layout). The 2026-06-27 fleet audit proved these are false-positive factories that
  need per-repo-type structural judgement (monorepo / Poetry / Node app). Out of scope.
- No new report format. Distribution findings reuse the existing `Violation` model and
  therefore every existing reporter (HTML/JSON/Markdown/SARIF) and the web dashboard.

## Architecture

### 1. `Scanner` protocol (the keystone)

Extract the currently-inlined local file access in `checker.py` behind a protocol:

```python
class Scanner(Protocol):
    def collect_files(self) -> Iterable[str]: ...
    def read_file(self, path: str) -> str | None: ...   # None = absent
```

- `LocalScanner` — current behaviour (filesystem `rglob` + `read_text`), unchanged.
- `OriginScanner` — reads `origin/<default-branch>` for one repo via
  `gh api repos/<owner>/<repo>/contents/<path>?ref=<default>` (base64 → text). Never
  touches a working tree, so it is immune to the stale-clone trap by construction. Covers
  repos that are not cloned locally (e.g. epub-sorter, wsmqtt-monitor).

This single seam is what makes `synthesize --source origin` possible and cures the
stale-clone bug for all of guideline-checker's multi-repo reporting, not just the new
checks.

### 2. Distribution check category

A new check category whose checks are **file presence / equality**, not per-line regex
over already-loaded content. Each check reads via the active `Scanner` and emits standard
`Violation`s (so all reporters + dashboard work for free):

| check id          | drift condition                                                        | fixable |
|-------------------|------------------------------------------------------------------------|---------|
| `standards-file`  | `.chrysa/STANDARDS.md` != canonical (`shared-standards/standards/STANDARDS.chrysa.md`) | yes |
| `claude-import`   | `CLAUDE.md` missing the delimited `@.chrysa/STANDARDS.md` block         | yes     |
| `precommit-pin`   | `.pre-commit-config.yaml` missing `chrysa/pre-commit-tools@<baseline rev>` | yes  |
| `license-present` | `LICENSE` absent                                                        | yes     |

Expected values are **derived from source** (DRY with `distribute-standards`): canonical
text, baseline `.pre-commit-config.yaml`, and the MIT `LICENSE` template all live in
`shared-standards`. The checks never re-encode expectations, so audit and distribution
cannot diverge.

### 3. Tri-state semantics mapped onto the violation model

- `OK` → no violation.
- `DRIFT` → a `Violation` (severity `error`/`warning`) with the offending path + check id.
- `NA` (not applicable) → **no violation** (never a false drift). Applicability is
  *declared*, not inferred from content.
- `ERROR` (fetch/auth failure) → a distinct `Violation` (severity `error`,
  rule `origin-fetch-failed`) so a transient API failure is never silently read as
  "compliant".

### 4. Fleet manifest + declarative applicability (`repos.yml`)

Origin mode iterates `shared-standards/repos.yml` (not local subdirs): `status: dev`
selects repos; each carries its default branch. `repos.yml` gains **optional,
backward-compatible** per-repo applicability flags, e.g. `personal: true` (excludes
`license-present`), `python: false` (excludes future python-specific checks). Absent =
all checks apply. A mis-classified repo is a one-line, reviewable fix in `repos.yml` —
applicability is data, not fragile heuristics. This is what would have prevented the
session's false positives (Node app flagged as "missing Python", monorepo / Poetry, etc.).

## CLI surface

Extend `synthesize`:

```
guideline-checker synthesize \
  --source local|origin \           # default: local (unchanged)
  --manifest <path/to/repos.yml> \  # required for --source origin
  --category distribution \         # restrict to the new checks
  [--json|--sarif|--md] [--fail-on error|warning] \
  [--fix] [--dry-run]
```

- `--source origin` + `--manifest` ⇒ iterate repos.yml via `OriginScanner`.
- Exit code via existing `--fail-on` threshold.

## `--fix` remediation (new capability)

A **fixer registry** keyed by check id (mirrors the existing `_AST_CHECKS` / scanner
registries):

- `license-present` → create `LICENSE` from the shared-standards MIT template.
- `claude-import`   → inject the delimited import block into `CLAUDE.md`.
- `standards-file`  → write the canonical content to `.chrysa/STANDARDS.md`.
- `precommit-pin`   → add/repair the `chrysa/pre-commit-tools` pin.

Mechanism (per repo, via `gh api`): create a branch off the default branch, PUT the
fixed file(s), open **one PR per repo** bundling that repo's distribution fixes.
**Never merges.** `--dry-run` previews the planned PRs. Idempotent: skip if the branch/PR
already exists.

Write-safety: guideline-checker is read-only today; `--fix` is strictly opt-in,
dry-run-able, one PR per repo, and never merges. Any force-merge stays a separate manual
decision (and is currently gated anyway by the org-wide GitHub Actions billing block).

## Testing strategy

- `OriginScanner` (the `gh api` boundary) is the mock seam: tests inject canned
  origin file contents to exercise `OK` / `DRIFT` / `NA` / `ERROR` per check.
- Fixers: mock the `gh api` branch/PUT/PR calls; assert the right PR payloads, idempotency.
- pytest + pytest-mock, `tests/constants.py` for paths/ids, mypy strict, ruff 0 warnings,
  coverage ≥ 85% (chrysa gate).

## Reports & dashboard

Distribution `Violation`s flow into HTML/JSON/Markdown/SARIF and the FastAPI web dashboard
unchanged (reporters are decoupled from the violation source). `--source origin` makes the
fleet view trustworthy.

## Risks / open questions

- **GitHub API rate limits** for ~61 repos × ~4 files: batch politely, cache per run;
  consider a short on-disk cache keyed by (repo, default-branch sha).
- **Auth**: relies on `gh` being authenticated (same assumption as the rest of the fleet
  tooling). Surface a clear error if not.
- **Baseline-rev source for `precommit-pin`**: read the pinned rev from shared-standards'
  own `.pre-commit-config.yaml` so it tracks automatically.
- **`shared-standards` location**: the manifest + canonical + templates live in a sibling
  repo; origin mode reads them from a configurable path (default: sibling `shared-standards`),
  or fetches them via `gh api` too for a fully clone-free run.
