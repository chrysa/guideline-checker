# guideline-checker

[![CI](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://github.com/chrysa/guideline-checker)
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
- **Autofix** — `guideline-checker fix` (or `check --fix`) rewrites the working tree for rules that declare a mechanical `fix:`; `--dry-run` previews the diff.
- **Committed config** — pin `fail_on`, `exclude`, `max_file_size`, `linters`, and `baseline` in a `[tool.guideline-checker]` table so every run agrees; CLI flags still override per-invocation.
- **Rule packs & inheritance** — organise rules into reusable packs; `extends:` inherits a base from any file and `include:` activates a pack's rules where you want them.
- **Multi-repo `synthesize`** — one rolled-up HTML report across every repo in a workspace.
- **Optional external linters** — fold `ruff`, `mypy`, or `eslint` results into the same report via `--linters`.
- **Four report formats** — HTML (color-coded, grouped by rule source), JSON (CI artifact), Markdown (PR comments), SARIF 2.1.0 (GitHub Code Scanning).
- **CI-friendly exit codes** — exits `1` when violations meet the `--fail-on` threshold.

## Installation

Not published on PyPI — distribution is the pre-commit hook (by git ref), the ghcr Docker image, and installing from source. Pick the channel that fits:

```bash
# From the git repo (pipx keeps it isolated)
pipx install 'git+https://github.com/chrysa/guideline-checker.git'

# Or as a pre-commit hook — see "As a pre-commit hook" below

# Or the container image
docker run --rm -v "$PWD:/repo" -w /repo ghcr.io/chrysa/guideline-checker check
```

From source (with dev tooling):

```bash
pip install -e '.[dev]'
```

Requires Python ≥ 3.14. The core CLI has two runtime dependencies (`PyYAML` for the YAML rule referential, `tree-sitter` for JS/TS AST detection); the optional web dashboard needs the `web` extra (see below).

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

### Autofix (`fix` / `check --fix`)

Rules that declare a mechanical `fix:` block can be applied to the working tree — the checker rewrites only the lines that actually fired.

```bash
guideline-checker fix                      # apply every rule's fix in place
guideline-checker fix --dry-run            # preview a unified diff, write nothing
guideline-checker check --fix              # same, from the check subcommand
```

A `fix:` supports three deterministic, idempotent operations (see [ADR D-0007](DECISIONS.md)):

```yaml
fix:
  op: remove_line                          # delete the whole flagged line
# or
  op: replace
  from: "yaml.load("
  to: "yaml.safe_load("
# or
  op: regex_replace
  pattern: "\\bvar\\b"
  replacement: "const"
```

Fixes are mechanical only — no semantic or LLM rewriting. Structural (AST-detected) rules ship no `fix:` and stay flag-only. After a real apply the checker re-scans and the exit code reflects what remains. Shipped fixes today: `py-safe-yaml` (→ `safe_load`), `py-no-debugger` (drop the line), `ts-no-var` (→ `const`).

### Baseline (adopt on a legacy repo)

Turning the gate on for an existing codebase surfaces its whole backlog at once. A **baseline** records the violations you currently accept so the gate fails only on *new* ones — adopt the checker from day one without a mass cleanup first.

```bash
# 1. Snapshot the current violations (exits 0, writes no gate)
guideline-checker check --write-baseline .guideline-baseline.json

# 2. Commit the baseline, then run against it — only NEW violations fail
guideline-checker check --baseline .guideline-baseline.json --fail-on error
```

Fingerprints are content-based (`rule id` + repo-relative path + the matched line text), not line-number based, so an edit that shifts a baselined violation up or down the file does not resurface it. Introduce a genuinely new violation and the gate fails as usual. Regenerate the baseline (step 1) after you fix a batch, to ratchet the accepted set down over time.

### Project configuration (`[tool.guideline-checker]`)

Pin the check behaviour in version control so every run — local, pre-commit, CI — agrees. Add a `[tool.guideline-checker]` table to `pyproject.toml` (or a `.guideline-checker.toml` at the project root):

```toml
[tool.guideline-checker]
fail_on = "warning"                      # error (default) | warning | never
exclude = ["tests", "scripts/**"]        # same globs as --exclude
max_file_size = 300000                   # bytes; larger files are skipped
linters = ["ruff", "mypy"]               # external linters to fold into the report
baseline = ".guideline-baseline.json"    # path (relative to the project root)
```

Precedence is **CLI flag > environment variable > config file > built-in default**, so a committed config sets the team baseline while any single run can still override it. Unknown keys and values of the wrong type are ignored with a warning — they never crash the run. `pyproject.toml` takes precedence over `.guideline-checker.toml` when both declare the table.

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

#### Inheritance and rule packs (`extends:` / `include:`)

A rule can inherit from another with `extends: <base-id>` — scalar fields fall through from the base, `detect:` patterns are unioned, and the child overrides what it declares. Bases may live in **any** file, so shared bases are reusable across the referential ([ADR D-0008](DECISIONS.md)):

```yaml
# guidelines/packs/security-strict.yml — a library, not auto-loaded
language_target: "*"
rules:
  - id: base-weak-hash
    abstract: true            # a template: available to extend, never emitted itself
    category: security
    severity: warning
    rule: "Use a strong hash (SHA-256+), never a broken algorithm"
    detect: { forbid: ["hashlib.md5(", "hashlib.sha1("] }
  - id: pack-no-pickle-loads
    category: security
    severity: error
    rule: "Never unpickle untrusted data with pickle.loads"
    detect: { forbid: ["pickle.loads("] }
```

```yaml
# guidelines/languages/python.yml
include:
  - packs/security-strict.yml   # activate the pack's concrete rules here
rules:
  - id: py-no-weak-hash
    extends: base-weak-hash      # inherit the base cross-file, tighten severity
    severity: error
```

Files under `guidelines/packs/` are **not** auto-loaded — a pack's abstract bases are always available to `extends:`, but its concrete rules become active only in a file that `include:`s it. `include:` paths are relative to `guidelines/`. A cross-file `extends:` cycle is a hard error.

### Shipped rule catalog

Every `languages/` rule below carries a working `detect:` block (AST, scanner, or `forbid`/regex) and is enforced. The `ai-models/` rules are **advisory**: they express provider conventions the checker surfaces but cannot yet detect mechanically — `guideline-checker web` reports them as `advisory` (not `dead`), and wiring detectors onto them is tracked work, not a shipped guarantee. Author your own by dropping a file in `guidelines/<dimension>/`.

| dimension | rule id | severity | detects |
|-----------|---------|----------|---------|
| transverse | `secrets-via-env` | error | hardcoded high-entropy secret assignment |
| transverse | `no-fixme-markers` | info | `FIXME` markers (comments included) |
| python | `py-pydantic-v2` | error | Pydantic v1 imports / `@validator` |
| python | `py-async-fastapi` | warning | non-`async` FastAPI route handler |
| python | `py-structured-logging` | warning | `print(` / `pprint(` |
| python | `py-no-mutable-default` | warning | mutable default argument |
| python | `py-no-bare-except` | warning | bare `except:` |
| python | `py-no-wildcard-import` | warning | `from x import *` |
| python | `py-no-eval-exec` | error | `eval(` / `exec(` |
| python | `py-no-shell-true` | error | `subprocess(..., shell=True)` |
| python | `py-no-os-system` | error | `os.system(` |
| python | `py-safe-yaml` | warning | unsafe `yaml.load(` |
| python | `py-no-debugger` | warning | `breakpoint(` / `pdb.set_trace(` |
| python | `py-provider-sdk-direct` | error | direct vendor LLM SDK import (`openai` / `anthropic` / … — Mark-L LLM001) |
| python | `py-no-weak-hash` | error | `hashlib.md5(` / `sha1(` (extends the security pack) |
| pack | `pack-no-pickle-loads` | error | `pickle.loads(` (via `include: packs/security-strict.yml`) |
| typescript | `ts-strict-types` | error | the `any` type |
| typescript | `ts-no-suppressions` | warning | `@ts-ignore` / `@ts-nocheck` |
| typescript | `ts-no-non-null-assertion` | warning | postfix `x!` |
| typescript | `ts-no-console-log` | warning | `console.log` / `console.debug` |
| typescript | `ts-no-var` | warning | `var` declarations |
| typescript | `ts-provider-sdk-direct` | error | direct vendor LLM SDK import (`openai` / `@anthropic-ai/sdk` / … — Mark-L LLM001) |
| typescript | `ts-no-debugger` | warning | `debugger;` |
| typescript | `ts-no-eval` | error | `eval(` |
| react | `react-hooks-top-level` | error | conditional/looped hook call |
| react | `react-stable-keys` | warning | array-index JSX `key` |
| react | `react-no-inline-component-defs` | warning | component defined inside another |
| react | `react-effect-deps` | warning | hook missing a dependency array |
| react | `react-no-dangerous-html` | error | `dangerouslySetInnerHTML` |
| react | `react-no-finddomnode` | warning | deprecated `findDOMNode` |

## Development

```bash
pip install -e '.[dev]'
pytest                       # coverage gate: 85%
ruff check . && ruff format .
mypy guideline_checker
```

## License

MIT — see [LICENSE](LICENSE).
