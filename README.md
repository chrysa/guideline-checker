# guideline-checker

[![CI](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/guideline-checker)](https://pypi.org/project/guideline-checker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![SonarCloud](https://sonarcloud.io/api/project_badges/measure?project=chrysa_guideline-checker&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=chrysa_guideline-checker)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

Turn the coding rules you already wrote for AI agents into an enforceable lint pass. `guideline-checker` reads your existing instruction files — GitHub Copilot instructions, `CLAUDE.md`, `AGENTS.md` — extracts the rules from them, scans your source tree for violations, and produces compliance reports (HTML, JSON, Markdown, SARIF). It runs as a CLI, a pre-commit hook, or a GitHub Action, so the conventions your agents are told to follow are actually checked.

## Who it's for

Teams and solo developers who maintain AI-agent instruction files (`.github/instructions/*.instructions.md`, `CLAUDE.md`, `AGENTS.md`) and want those same conventions enforced on humans and CI — without writing a custom linter per rule.

## Features

- **Multi-source rule discovery** — automatically loads rules from `.github/instructions/*.instructions.md`, `.github/copilot-instructions.md`, `CLAUDE.md` / `.claude/CLAUDE.md`, and `AGENTS.md` / `.claude/agents/*.md`. One set of guidelines, no duplication.
- **Pattern-based anti-pattern detection** — recognises rule phrasing (e.g. "no print", "no bare except", "no `any`", "no `@ts-ignore`", "run as non-root", "no `:latest` tag", "no hardcoded secrets", max file/function length) and flags matching lines with `error` / `warning` / `info` severity.
- **Structured YAML referential** — author rules as data in `guidelines/<dimension>/*.yml` (dimensions `ai-models/` and `languages/`) with explicit `id` / `category` / `severity` / `rationale`. The explicit `severity` overrides the phrasing-derived default, a shared `categories.yml` keeps dimensions from diverging, and discovery is by folder convention (drop a file → it's loaded).
- **`applyTo` scoping** — rules apply only to the files their glob targets; generic rules are auto-narrowed by filename (a `python.instructions.md` rule won't fire on JSON files).
- **Inline suppression** — add a `guideline: disable` comment on any line to skip it.
- **`--diff` mode** — check only files changed in the git working tree for fast incremental pre-commit runs.
- **Baseline adoption** — `--write-baseline` / `--baseline` fail only on *new* violations, so you can turn the gate on for a legacy repo without fixing its whole backlog first.
- **Multi-repo `synthesize`** — one rolled-up HTML report across every repo in a workspace.
- **Optional external linters** — fold `ruff`, `mypy`, or `eslint` results into the same report via `--linters`.
- **Four report formats** — HTML (color-coded, grouped by rule source), JSON (CI artifact), Markdown (PR comments), SARIF 2.1.0 (GitHub Code Scanning).
- **CI-friendly exit codes** — exits `1` when violations meet the `--fail-on` threshold.

## Installation

```bash
pip install guideline-checker
```

From source (with dev tooling):

```bash
pip install -e '.[dev]'
```

Requires Python ≥ 3.14. The core CLI has a single runtime dependency (`PyYAML`, for the YAML rule referential); the optional web dashboard needs the `web` extra (see below).

## Usage

### Scaffold instruction files

```bash
guideline-checker init            # create default .github/instructions/ files
guideline-checker init --force    # overwrite existing files
```

### Check a project

```bash
# Scan the current directory; loads Copilot/CLAUDE/AGENTS sources by default
guideline-checker check

# Write reports
guideline-checker check --output report.html \
                        --json report.json \
                        --markdown summary.md \
                        --sarif results.sarif

# Fast incremental check of git-changed files only
guideline-checker check --diff

# Fail on warnings too (default fails on errors only)
guideline-checker check --fail-on warning

# Only read *.instructions.md from --instructions, ignore CLAUDE.md / AGENTS.md
guideline-checker check --no-multi-source --instructions .github/instructions/

# Include external linter results in the report
guideline-checker check --linters ruff mypy      # or --linters with no args to auto-detect

# Skip paths from the scan (repeatable; each value may be comma-separated)
guideline-checker check --exclude tests --exclude 'scripts/**,**/*.md'
```

`--exclude` takes globs relative to `--root`. A bare directory name (`tests`) excludes everything beneath it; `**` matches recursively (`scripts/**/*.py`). It also narrows `--diff` runs.

To scope the scan without passing `--exclude` on every run (e.g. for the pre-commit hook, which runs with no arguments), drop a **`.guidelineignore`** file at the project root — one glob per line, `#` comments and blank lines ignored, same pattern semantics as `--exclude`. Its patterns are merged with any `--exclude` values.

```gitignore
# .guidelineignore
tests
scripts/**
**/*.generated.ts
```

`--fail-on` accepts `error` (default), `warning`, or `never`.

### Baseline (adopt on a legacy repo)

Turning the gate on for an existing codebase surfaces its whole backlog at once. A **baseline** records the violations you currently accept so the gate fails only on *new* ones — adopt the checker from day one without a mass cleanup first.

```bash
# 1. Snapshot the current violations (exits 0, writes no gate)
guideline-checker check --write-baseline .guideline-baseline.json

# 2. Commit the baseline, then run against it — only NEW violations fail
guideline-checker check --baseline .guideline-baseline.json --fail-on error
```

Fingerprints are content-based (`rule id` + repo-relative path + the matched line text), not line-number based, so an edit that shifts a baselined violation up or down the file does not resurface it. Introduce a genuinely new violation and the gate fails as usual. Regenerate the baseline (step 1) after you fix a batch, to ratchet the accepted set down over time.

### Multi-repo synthesis

```bash
guideline-checker synthesize --workspace /path/to/workspace
# optional: --repos repo-a repo-b  --linters ruff  --instructions shared/.github/instructions/
```

Writes a per-repo `guideline-report.html` plus a combined `guideline-synthesis.html`.

### Fleet distribution audit (origin-side)

By default `synthesize` scans **local** working trees, which lag `origin`. To audit what each
repo actually carries on its **default branch** — immune to the stale-clone trap — point it at
`origin` via a `repos.yml` manifest and a `shared-standards` checkout:

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

It reads `origin/<default>` for every `status: dev` repo via the `gh` API (so `gh` must be
authenticated) and checks four managed artifacts, reported as normal violations in the same
HTML/JSON/Markdown/SARIF reporters and the web dashboard:

| check id          | drifts when …                                                          | auto-fixable |
|-------------------|------------------------------------------------------------------------|--------------|
| `standards-file`  | `.chrysa/STANDARDS.md` differs from the canonical `STANDARDS.chrysa.md` | yes          |
| `claude-import`   | `CLAUDE.md` is missing the `@.chrysa/STANDARDS.md` import               | report-only  |
| `precommit-pin`   | `.pre-commit-config.yaml` is missing the `chrysa/pre-commit-tools` pin  | report-only  |
| `license-present` | `LICENSE` is absent                                                     | yes          |

A repo whose `origin` cannot be reached (auth/API failure) yields an `origin-fetch-failed`
error finding rather than being silently read as compliant.

`--fix` opens **one PR per drifting repo and never merges** (use `--dry-run` to preview). Only
`standards-file` and `license-present` are auto-fixed (safe whole-file writes); `claude-import`
and `precommit-pin` are report-only because they need content-aware edits.

#### `repos.yml` manifest

Origin mode iterates the fleet manifest (`status: dev` repos only). Per-repo applicability is
declared in an optional `distribution:` opt-out block — absent keys default to applicable:

```yaml
repos:
  - name: my-resume
    status: dev
    distribution:
      license: false      # personal-content repo — no blanket MIT LICENSE expected
```

### Pre-commit hook

```yaml
- repo: https://github.com/chrysa/guideline-checker
  rev: v1.0.0
  hooks:
    - id: guideline-check                 # check --fail-on error
    # - id: guideline-check-warning       # stricter: also fails on warnings (stage: manual)
```

Pass extra args to scope it, e.g. `args: [check, --diff, --fail-on, warning]`.

### GitHub Action

The repo ships a composite action (`action.yml`) that installs the tool, runs `check`, uploads SARIF to GitHub Code Scanning, attaches the Markdown + JSON reports as an artifact, and — when a central server is configured — pushes the JSON report to it.

```yaml
- name: Guideline check
  uses: chrysa/guideline-checker@v1
  with:
    fail-on: error          # error | warning | never
    # exclude: 'tests, scripts/**'   # comma-separated globs to skip
    # instructions: ''      # defaults to <root>/.github/instructions
    # upload-sarif: 'true'
    # central-server: ''     # e.g. https://guidelines.example.com — set to enable the push
    # central-api-key: ''    # X-Api-Key for the central server (use a secret)
```

When `central-server` is set, the action runs `guideline-checker push` after the check — even when the check failed on violations, so the server always gets the latest state. The push is best-effort (`continue-on-error`) so a flaky server never breaks your CI.

Outputs: `violations` (count) and `sarif-path`.

### Python API

```python
from pathlib import Path
from guideline_checker.checker import run_checks
from guideline_checker.reporters.html import HtmlReporter

results = run_checks(root=Path("."), all_sources=True)
HtmlReporter().write(results=results, output_path=Path("report.html"), root=Path("."))
```

`run_checks` returns a list of `RuleResult`, each holding the source `InstructionFile` and its `Violation`s. Other reporters: `reporters.json_reporter.JsonReporter`, `reporters.markdown.MarkdownReporter`, `reporters.sarif.SarifReporter`.

## Web dashboard (optional)

Install the extra and serve the FastAPI app to browse and trigger scans from a browser:

```bash
pip install 'guideline-checker[web]'
uvicorn guideline_checker.web.app:app --host 0.0.0.0 --port 8000
```

Configure via environment variables (see [`.env.example`](.env.example)): `SCAN_ROOT`, and `AUTH_MODE` = `disabled` | `api_key` (default) | `local` | `ldap` | `oidc`, with the matching credential vars (`API_KEY`, `LOCAL_USERNAME`/`LOCAL_PASSWORD`, `LDAP_*`, `OIDC_*`).

## Central server (multi-repo)

The single-repo dashboard above shows one project. The **central server** aggregates compliance across *every* repo: each repo runs `check --json` in CI and pushes the report; the server keeps the latest snapshot per repo and renders a combined view.

Run the server (needs the `web` extra; reuses the same `AUTH_MODE` contract):

```bash
guideline-checker central --store ./central-store --host 0.0.0.0 --port 8090
```

It exposes `POST /api/ingest` (auth), `GET /api/repos` (latest snapshot + error trend per repo), `GET /api/repos/{repo}`, `GET /api/repos/{repo}/history?limit=N` (compliance over time), and an aggregated dashboard at `/` (with ▲/▼ trend arrows). Reports are stored as one JSON file per repo under `CENTRAL_STORE` (default `./central-store`), plus a bounded `history/<repo>.jsonl` log.

Push a report from a repo's CI (or anywhere):

```bash
guideline-checker check --root . --json guideline-report.json
guideline-checker push --server https://guidelines.example.com \
                       --report guideline-report.json \
                       --api-key "$GUIDELINE_API_KEY"
# --repo / --commit / --branch default to the git remote name and current HEAD.
```

`push` uses only the standard library, so it works without the `web` extra. In a GitHub Actions step it is a single command after the check; the server URL and key live in repo/org secrets.

## How rules are extracted

Rules are pulled from markdown bullet lists, numbered lists, and table rows containing constraint keywords (`must`, `never`, `always`, `forbidden`, `required`, `mandatory`). For `.instructions.md` files, the YAML frontmatter `applyTo` glob scopes which files a rule set targets. Detection is pattern-based (line and whole-file matching), not a full AST analysis.

### Structured YAML referential

Alongside markdown sources, a `guidelines/` directory at the project root provides a structured, machine-authored rule set, discovered by folder convention:

```
guidelines/
  categories.yml         # shared category registry (validated against)
  ai-models/             # rules keyed by model_target (claude, gpt, …)
    _common.yml          #   model_target: "*"  → transverse
    claude.yml
  languages/             # rules keyed by language_target (python, typescript, react)
    _common.yml          #   language_target: "*"  → transverse
    python.yml
```

Each rule is a mapping:

```yaml
language_target: python
rules:
  - id: py-async-fastapi      # stable, unique (kebab-case)
    category: stack           # must exist in categories.yml
    severity: warning         # error | warning | info — overrides the phrasing default
    rule: "Define FastAPI route handlers as async def"
    rationale: "Consistent non-blocking I/O"
```

The `<dimension>` directory sets the target field (`model_target` / `language_target`); a rule may override it (e.g. `"*"` for a transverse rule living in a targeted file). The target maps to an `applyTo` glob (`python → **/*.py`, `typescript → **/*.ts,**/*.tsx`, `*`/model → `**/*`). Unknown categories fail the load; duplicate `id`s resolve first-match-wins and are logged. The referential is filesystem-only — no external registry, no source links.

#### Declarative detectors (`detect:`)

By default a rule only produces violations when its prose matches one of the checker's built-in trigger phrases (`"no print"`, `"no bare except"`, …). A rule whose wording the checker doesn't recognise loads and is reported, but silently never fires. Add an optional `detect:` block to make a rule carry its own detector — so any rule fires without a code change:

```yaml
rules:
  - id: py-pydantic-v2
    category: stack
    severity: error
    rule: "Use Pydantic v2 models exclusively; v1 syntax is forbidden"
    detect:
      forbid:                       # per-line, case-insensitive substrings
        - "from pydantic import validator"
        - "@root_validator"
      forbid_regex:                 # per-line, case-insensitive regexes
        - "\\.parse_obj\\("
      file_regex:                   # whole-file regexes (MULTILINE | IGNORECASE) — structural/multiline
        - "@(?:app|router)\\.(?:get|post)\\([^\\n]*\\)\\s*\\n\\s*def\\s"
      ast:                          # named Python AST checks (precise; .py files only)
        - pydantic-v1
      match_in_comments: false      # default false; applies to forbid / forbid_regex
```

All keys are optional but a `detect:` block must declare at least one pattern (or `ast` check). A declared violation inherits the rule's own `severity`. Inline `guideline: disable` suppression and the `applyTo` scoping apply to declared detectors exactly as to the built-in ones. Phrase-derived detection still runs alongside, so the two can coexist on one rule.

**`ast` checks** parse the file with Python's `ast` module instead of matching text, so they fire only on the real construct — never on the same text inside a string literal or comment, and robustly across whitespace/multiline forms. They run on `.py` files only; a file that doesn't parse yields nothing. Available checks:

| name | flags |
|------|-------|
| `pydantic-v1` | Pydantic v1 imports (`validator`, `root_validator`, `BaseSettings`, `pydantic.v1.*`) and `@validator` / `@root_validator` decorators |
| `sync-fastapi-route` | a route decorator (`@app.get` / `@router.post` …) applied to a non-`async def` handler |
| `mutable-default-arg` | a function parameter whose default is a shared mutable (`[]`, `{}`, `set()`, `list()`, `dict()`) |

## Development

```bash
pip install -e '.[dev]'
pytest                       # coverage gate: 85%
ruff check . && ruff format .
mypy guideline_checker
```

## License

MIT — see [LICENSE](LICENSE).
