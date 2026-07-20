# CLAUDE.md — guideline-checker

> **Claude Code**: also read `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` for code specifications.

## Vision

Turn the coding rules you already wrote for AI agents (`.github/instructions/*.instructions.md`,
`CLAUDE.md`, `AGENTS.md`) into an enforceable, **honest** lint pass — CLI, pre-commit hook, or
GitHub Action, plus a local **workshop** web UI.

Honest means the tool never passes green over a rule that cannot detect anything. Each rule
carries a **health** state (`rule_health.py`): `proven` (fires on real code), `armed` (has a
detector, no match), `dead` (a YAML rule with no detector — a real defect), or `advisory` (a
markdown bullet surfaced but never enforced). The workshop closes the loop:
**detect → propose a detector (heuristic, then an optional LLM) → replay it in a sandbox for
proof → validate → write it into `guidelines/*.yml`.** The LLM only proposes; detection stays
deterministic and offline (see `DECISIONS.md`, ADR D-0010…D-0014).

## Usage

### As a pre-commit hook

Add to your `.pre-commit-config.yaml` (pin the current tag — the tool is **not** on PyPI):

```yaml
- repo: https://github.com/chrysa/guideline-checker
  rev: v1.11.3
  hooks:
    - id: guideline-check
```

The hook runs `guideline-checker check --fail-on error` on the whole project. It reads rules
from `.github/instructions/`, `.github/copilot-instructions.md`, `CLAUDE.md`, `AGENTS.md`, and
a `guidelines/<dimension>/*.yml` referential. Adopt on a legacy repo without a mass cleanup via
a baseline: `args: [check, --fail-on, error, --baseline, .guideline-baseline.json]`.

### As a CLI tool

Not published on PyPI — install from source or run the ghcr image:

```bash
pipx install 'git+https://github.com/chrysa/guideline-checker.git'
guideline-checker check --root . --fail-on error
guideline-checker check --root . --json report.json --output report.html
guideline-checker check --root . --write-baseline .guideline-baseline.json   # accept current, gate new
```

### Web workshop / dashboard

```bash
pipx install 'guideline-checker[web] @ git+https://github.com/chrysa/guideline-checker.git'
guideline-checker web --root . --port 8080   # http://127.0.0.1:8080
```

Scan → rule-health tiles → filterable rules table → click a rule → propose & replay → proof
(hits with file:line) before any write. The optional LLM backend is opt-in: `GC_CLAUDE=1`
(Claude CLI, default) or `GC_OLLAMA=1` (local Ollama). Auth is env-driven (`AUTH_MODE`,
`API_KEY`, … — see `.env.example`); `make web-up` runs the containerised equivalent.

## Structure

```
guideline_checker/
  checker.py            # Core deterministic check engine — runs rules against source files
  rule_health.py        # Rule health (proven / armed / dead / advisory) — no LLM
  proposer.py           # Proposer seam: HeuristicProposer + Ollama/Claude LLM backends
  sandbox.py            # Replay a proposed detector for proof, writing nothing
  persist.py            # Write a validated detector into guidelines/*.yml (dry-run diff)
  scanners.py           # Entropy secret-assignment scanner (detect.scan registry)
  ast_python.py         # Named Python AST checks (detect.ast)
  ast_javascript.py     # Named JS/TS AST checks via tree-sitter
  baseline.py           # Baseline adoption (accept current violations, gate new)
  cli.py                # CLI entry point — init/check/fix/synthesize/web/central/push
  hook.py               # Pre-commit hook entry point (delegates to cli.main)
  loader.py             # Instruction file loader/parser (markdown sources)
  guidelines.py         # Structured YAML rule referential loader (guidelines/<dimension>/*.yml)
  autofix.py            # Local declarative autofix (fix: block); fixers.py = remote drift PRs
  linters.py            # External linter integration (ruff / mypy / eslint / biome)
  reporters/            # html.py, synthesis_html.py, json_reporter.py, markdown.py, sarif.py
  web/
    app.py              # FastAPI app — dashboard + /api/scan|results|rules-health|propose|rules/detector
    static/index.html   # Single-page workshop UI (bundled via package-data)
    central.py          # FROZEN: multi-repo aggregate server (no active reporters)
    auth.py             # Pluggable auth (api_key / local / ldap / oidc)
guidelines/             # YAML rule referential: ai-models/ (advisory), languages/, packs/
.pre-commit-hooks.yaml  # Hook definition for pre-commit framework
tests/                  # pytest suite (test_rule_health, test_proposer*, test_sandbox, test_persist, …)
```

## Hook configuration

The `.pre-commit-hooks.yaml` defines:
- `id: guideline-check`
- `language: python` — installed in a virtualenv by pre-commit
- `pass_filenames: false` — runs on the whole project, not individual files
- `always_run: true` — runs even when no matching files are staged
- `args: [check, --fail-on, error]` — fails on first error-level violation

## Conventions

- Python 3.14 (CI matrix 3.12 + 3.14)
- Ruff for linting and formatting
- Mypy strict mode
- Pytest + pytest-cov for tests
- All code, comments, issues, PRs, and docs in English

## Local test procedure

All checks must go through `make` targets. Never invoke `ruff`/`pytest`/`mypy` directly on the host outside of the make wrapper.

```bash
# 1. Install
make install-dev

# 2. Full quality check (lint + format + typecheck)
make lint && make format-check && make typecheck

# 3. Run tests
make test                  # all tests
make test-cov              # with coverage report

# 4. Run all pre-commit hooks on every file
make pre-commit

# 5. Validate GitHub Actions workflows (requires actionlint)
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest

# 6. Quality gate (no-regression check)
make quality-gate-verify
```

### Regression gate (before every PR)
```bash
make lint && make test-cov && make quality-gate-verify
# Coverage must stay >= 85%. Lint warnings must be 0.
```

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **guideline-checker** (286 symbols, 465 relationships, 6 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/guideline-checker/context` | Codebase overview, check index freshness |
| `gitnexus://repo/guideline-checker/clusters` | All functional areas |
| `gitnexus://repo/guideline-checker/processes` | All execution flows |
| `gitnexus://repo/guideline-checker/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Skills

- `testing-pytest/SKILL.md` — pytest DDD + pytest-mock + constants (load when writing tests)

- `error-handling/SKILL.md` — FastAPI error handling + Sentry + logging (load when handling errors)

- `dockerfile-multistage/SKILL.md` — 4-stage Python 3.14 containers (load when editing Dockerfile)

- `clean-architecture/SKILL.md` — FastAPI module/layer structure (load when adding a domain feature)

- `async-patterns/SKILL.md` — async FastAPI + SQLAlchemy async sessions (load when writing async code)

- `api-design/SKILL.md` — REST standards + FastAPI patterns (load when designing endpoints)

Shared skills from `shared-standards/.claude/skills/`:

- `ui-ux/SKILL.md` — UX/UI/ergonomics across ALL surfaces (web, CLI, VS Code, Discord, desktop, game, agent) + WCAG 2.1 AA + dark mode + i18n FR+EN (load when building any human-facing surface)


<!-- chrysa:standards:start · managed by distribute-standards.sh · DO NOT EDIT -->
# chrysa — Transverse Standards

These conventions are identical across every chrysa repo. Repo-specific rules live in the
local `CLAUDE.md`; this file is the shared baseline imported by it.

## Cross-cutting stack (settled ADRs — do not relitigate)

| Layer            | Decision                                                        |
|------------------|----------------------------------------------------------------|
| Python           | 3.14 target (CI matrix 3.12 + 3.14)                            |
| FastAPI          | >= 0.115 + Pydantic v2                                          |
| Frontend         | React 19 + TypeScript 7 + Vite 8                                |
| UI               | shadcn/ui + Tailwind CSS                                        |
| State            | TanStack Query + Zustand                                        |
| DB               | PostgreSQL 16 + Redis 7                                         |
| ORM              | SQLAlchemy 2.0 async + Alembic                                  |
| Auth             | 4 modes: Google OAuth2 · local (bcrypt) · LDAP · VCS OAuth      |
| i18n             | react-i18next + fastapi-babel · FR + EN from V1                 |
| Monorepo         | Turborepo + pnpm workspaces                                     |
| Versioning       | GitVersion (semantic auto — never bump manually)               |
| Quality CI       | SonarCloud (0 hotspot · rating A)                               |
| Linting          | Ruff + Mypy (Python) · ESLint (TS)                             |
| Pre-commit       | detect-secrets + ruff + mypy + commitlint                      |
| Error handling   | withErrorHandling() → auto GitHub Issue on failure             |
| Hosting          | Kimsufi · Docker Compose (local) · Nginx · Certbot · Tailscale  |
| Monitoring       | Sentry + Uptime Kuma (self-hosted)                            |
| Agents           | Claude API (primary) · Ollama (fallback)                       |
| Orchestration    | LangGraph (stateful) · PydanticAI (structured outputs)         |

## Non-negotiable conventions

- **Language**: English — all code, comments, docs, instructions, and config files.
- **Commits**: Conventional Commits (`feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`).
- **Branches**: `feature/`, `bugfix/`, `chore/`, `hotfix/`, `release/` · default branch `develop`.
- **Merge**: squash merge only · force push forbidden · auto-merge requires CI + owner.
- **One PR per issue**, scoped tight. Every PR references an issue (`Closes/Fixes/Refs #N`).
  Exception: label `hotfix`. The `enforce-issue-link` workflow is a blocking status check.
- **Tests: pytest only** — assert-style test functions and `pytest-mock` (`mocker`
  fixture: `mocker.patch`, `mocker.AsyncMock`) for all mocking. The stdlib **`unittest`
  framework (`unittest.TestCase`) and `unittest.mock` imports are forbidden** — no
  `import unittest`, no `from unittest.mock import …`. See the `testing-pytest` skill.
- **Dark mode** mandatory from V1. **Accessibility** WCAG 2.1 AA.
- **UI state survives reload & focus** — human-facing surfaces persist their navigation
  and view state (active tab/section, selected sub-view, active context/filters) so a
  **manual reload keeps the current page** — the user lands exactly where they were, never
  reset to a default. Persist to `localStorage` (or the URL for shareable state), guarded
  by a validator that discards stale/removed values. Interface or state changes must
  **propagate across the app's own tabs/windows and on refocus/reload**: listen to the
  browser `storage` event and re-read on `window` `focus`, so a view opened while hidden
  never shows stale state after the user comes back. A reload that loses the user's place,
  or a change that fails to propagate on focus/reload, is a bug.
- **Notion logging**: every advancement and modification (progress, decisions, state
  changes) is logged in Notion — the single source of truth. Run `@notion-sync` after any
  state change; in case of conflict between local docs and Notion, Notion wins.
- **No hardcoded constants** in code — neither backend (Python) nor frontend (TS).
  All constants and config values (thresholds, business rules, labels, URLs, magic
  numbers) live in **external YAML files** and are loaded at runtime. Code reads them
  through a typed loader (Pydantic Settings backend · generated typed module frontend),
  never as inline literals. Only language-level enums (e.g. `status.HTTP_*`) are exempt.
- **Semantic URLs & code** — URLs are resource-oriented and human-readable: lowercase,
  hyphenated, plural-noun collections, no verbs or actions in the path (`GET /invoices/42`,
  never `/getInvoice?id=42`); REST shapes follow the `api-design` skill. Code is
  self-describing: intention-revealing names over comments, semantic HTML elements
  (`<nav>`, `<button>`, `<main>`, `<header>`…) never a `<div>` wired as a control, and
  ARIA used only to fill gaps native semantics cannot express.
- **URL-addressable frontend navigation — mandatory.** Every navigable view/route/tab/
  detail is a **real, semantic URL** (`/projects/42/settings`, not `/#` or a modal with no
  address). Navigating **must change the URL** via the router (History API `pushState`), so:
  1. the change is **recorded in browser history** — Back/Forward move between views, never
     trap or reload the app;
  2. a link is **right-clickable / middle-clickable / ⌘-clickable → open in a new tab**, which
     means it is a genuine `<a href>` (or the router's `<Link>`), **never** a `<div>`/`<button>`
     with an `onClick` that only mutates state;
  3. the URL is **deep-linkable & shareable** — pasting it in a fresh tab lands on the exact
     same view (route params + query for filters/selection/pagination), reproducible without
     prior in-app state.
  Ephemeral UI (transient toasts, open/closed of a purely local menu) may stay stateless, but
  anything a user would bookmark, share, or reload into is a route. This complements
  *UI state survives reload & focus*: persisted view-state that has an addressable identity
  belongs in the URL, not only `localStorage`.
- **No virtualenv in a repo — ever.** `venv/`, `.venv/`, `env/` are **forbidden** inside a
  project tree. Python runs in the Docker image (deps baked into the image layer, or a named
  volume for editable installs). A committed or on-disk virtualenv is a bug, not a setup step.
  The only sanctioned local Python is a **uv/pipx tool env stored outside the repo** (e.g.
  `~/.local`, `$UV_CACHE_DIR`), never a folder living next to the source.
- **Tool caches & deps never touch the project tree.** `__pycache__`, `.pytest_cache`,
  `.ruff_cache`, `.mypy_cache`, `.benchmarks`, `node_modules`, and pip/npm/uv download caches
  are regenerable machine artifacts — they must be **invisible in the repo working copy**.
  Enforcement is three-layered and all three are mandatory:
  1. **`.gitignore`** carries the managed canonical block (`templates/gitignore.canonical`) so
     they are never tracked. Appended, not hand-maintained.
  2. **Docker caches live in named volumes**, and every cache-dir env var points **outside**
     the bind mount so nothing is written back into the mounted source:
     ```yaml
     # docker-compose: a container that bind-mounts the repo
     services:
       tests:
         user: "${UID:-1000}:${GID:-1000}"   # host UID — NEVER `user: root`
         environment:
           PYTHONPYCACHEPREFIX: /caches/pycache
           RUFF_CACHE_DIR: /caches/ruff
           MYPY_CACHE_DIR: /caches/mypy
           PYTEST_ADDOPTS: -p no:cacheprovider  # or PYTEST_CACHE_DIR under /caches
           npm_config_cache: /caches/npm
         volumes:
           - .:/code
           - tool-caches:/caches          # named volume, persists across runs
           - node-modules:/code/node_modules   # deps in a volume, shadowing the mount
     volumes:
       tool-caches:
       node-modules:
     ```
  3. **Any container that bind-mounts a repo runs as the host UID** (`user: "${UID}:${GID}"`),
     **never `user: root`** — root-owned artifacts written into a bind mount are unremovable
     without `sudo` and are treated as a defect. Root user is allowed only for containers with
     **no** repo bind mount (e.g. `.:/code` absent).
  Regenerable artifacts already in a repo are purged with `scripts/purge-artifacts.sh`.
- **Dockerfiles are multi-stage, with a `production` and a `dev` stage — mandatory.** Every
  application Dockerfile uses named build stages so a single file yields both runtime and
  developer images (`docker build --target production` / `--target dev`). Minimum stages:
  a shared `base` (interpreter + OS deps), a `builder`/`deps` (compiles/installs dependencies),
  a **`production`** target (slim runtime — no dev tooling, non-root `USER`, only the built
  artifact), and a **`dev`** target (production + test/lint/debug tooling, editable install,
  live-reload). A single-stage Dockerfile, or one missing either `production` or `dev`, is a
  defect. Compose services select the target explicitly (`build.target: production|dev|tests`).
  Canonical shape + Python 3.14 example: the `dockerfile-multistage` skill.
  *Exemption — container-collection repos:* a repo whose product **is** a set of standalone
  utility/tool container images (e.g. `usefull-containers`), not one application, is exempt from the
  `production`+`dev` two-stage rule per image — each image is single-purpose. Such images still must
  not embed a reverse proxy and still run as non-root where they bind-mount host paths.
- **App containers ship the app only — the platform layer is the owner's responsibility.** An
  application image/container **never embeds a reverse proxy** (nginx/Traefik/Caddy/HAProxy as a
  TLS-terminating or routing front). The app container exposes its own port and speaks plain HTTP;
  routing, TLS, virtual hosts, and load-balancing live in the **platform layer** (the owner's
  Nginx/Traefik + Certbot on the host, or `deploy/k8s/` ingress), out of the app image. A static
  frontend may use a minimal internal web server to serve its own built assets, but it does **not**
  proxy other services. Baking a reverse proxy into an app container is a defect (couples the app to
  infra, duplicates the platform, and breaks the ownership boundary).
- **Dev stage must hot-reload.** The `dev` target/service provides live auto-reload so a source edit
  is reflected without a manual rebuild/restart: backend `uvicorn --reload` (or the framework's
  autoreload), frontend the dev server with HMR (`vite`/`npm run dev`), watched via the compose
  `develop.watch` sync or a source bind mount. A `dev` image identical to `production` (no reload) is
  not a dev image.

## Quality gates

- Test coverage **>= 85%** by default. A repo may override upward, never below 80%.
- Lint warnings: **0**. Mypy clean. SonarCloud rating **A**, 0 security hotspot.
- Max function lines 50 · max file lines 500 · cyclomatic complexity heuristic <= 10.

## Makefile targets

- **Referential**: `Forge-Stack-Workshop/base-makefile` (`Makefile.basic`, `Makefile.python`,
  `Makefile.with-sub-folder`) is the single source of truth for target names and behaviour.
- **Canonical naming** — follow base-makefile verbatim, one word where it is one word:
  `typecheck` (**never** `type-check`), `test-cov`, `format-check`, `quality-gate-verify`,
  `docker-test`, `ci`. Renaming or aliasing a canonical target is forbidden.
- **Mandatory socle** — every application repo MUST expose, with these exact names and intent:
  `help install install-dev lint format format-check typecheck test test-cov pre-commit clean
  ci quality-gate-baseline quality-gate-verify`. Non-applicative repos (pure infra/Helm/Terraform,
  config-only, docs) are exempt from the language-specific targets (`typecheck`, `test-cov`) but
  still expose `help lint pre-commit clean`.
- **Docs must match** — every `make <target>` cited in `CLAUDE.md` or `README.md` MUST exist in
  the Makefile (no `make type-check` when the target is `typecheck`).
- **Recipe style** — prefix every recipe line with `@`; add `## Description` after each target so
  it appears in `make help`.

## Shared skills (load on demand from shared-standards/.claude/skills/)

- `testing-pytest` — pytest DDD + pytest-mock + constants (writing tests)
- `dockerfile-multistage` — 4-stage Python 3.14 containers (editing Dockerfile)
- `api-design` — REST standards + FastAPI patterns (designing endpoints)
- `async-patterns` — async FastAPI + SQLAlchemy async sessions (async code)
- `clean-architecture` — FastAPI module/layer structure (adding a feature)
- `error-handling` — FastAPI errors + Sentry + logging (handling errors)
- `contract-testing` — library contract / breaking-change tests (@chrysa/* releases)
- `agent-patterns` — LangGraph + PydanticAI + Claude API (building agents)
- `ui-ux` — UX/UI/ergonomics + WCAG 2.1 AA + dark mode + i18n (human-facing surfaces)

## Error handling pattern (all automations)

```text
try:    fn()
except: gh issue create --title "[chrysa] failure" --label "chrysa-error"
```

## Observability — Sentry → GitHub issues (norm)

Every status:dev repo ships a Sentry project, and **a new Sentry issue automatically opens a
GitHub issue** via Sentry's native GitHub integration. No relay, no PAT in the repo — the
integration owns the link, so a Sentry issue maps to exactly one GitHub issue (no duplicates).

Mechanism: a per-project Sentry **issue alert rule** with
condition `FirstSeenEventCondition` (a new issue is created) and action
`GitHubCreateTicketAction` targeting `chrysa/<repo>`, labels `sentry`, `bug`.
Provision it across all projects with
`shared-standards/scripts/sentry-github-issues.sh` (idempotent, `--dry-run` first).

Per-project activation checklist:

1. Org GitHub integration installed once in Sentry (Settings → Integrations → GitHub) with
   access to the chrysa repos.
2. The repo has a Sentry project whose slug matches the repo name.
3. The auto-issue alert rule exists (run the provisioning script, or add it in
   Alerts → Create Alert → Issues → action "Create a GitHub issue").
4. The GitHub repo has a `sentry` label (CI label sync provides it).

## Governance — strategic pillars & ADR format

Five non-negotiables hold across every chrysa project, whatever the stack. Breaking one
requires an ADR with a kill-test, not a shrug.

1. **LLM-provider independence** — no vendor SDK in business code; inference goes through a
   local port with **≥2 real, tested adapters** (e.g. Claude + a local model). A prompt that
   only works on one vendor is a bug, not a feature.
2. **GAFAM independence** — every managed-cloud dependency has a documented self-hosted exit
   path; the cloud SDK stays confined to an adapter (`BlobStore`, not `S3Client`).
3. **Portable personalisation data** — all user/personal data is exportable to an open format
   (JSON/SQLite) by a documented command; `export → import → export` is idempotent (tested).
   A stored-but-unexportable field needs an ADR.
4. **k8s config in-project** — manifests live in `deploy/k8s/` of the repo; nothing exists
   only inside a running cluster.
5. **Adaptation layer** — no third-party lib/API/service is imported by the domain directly;
   it goes through an adapter whose port is written in the domain's language, not the vendor's.

**ADR format (refutable).** Any structural decision — new external dependency, LLM/cloud
provider choice, breaking public-API change, data-model change, or a pillar exception — gets
one ADR under `docs/adr/` (series named in the local `CLAUDE.md`). Beyond the classic fields,
every chrysa ADR carries three that make it falsifiable:

- **Fatal hypothesis** — the single, falsifiable belief whose falsity invalidates the decision.
  One only; about the real world (cost, latency, a third party), not an internal intention.
- **Kill-test** — the observable, dated signal that proves it wrong: what to measure, which
  threshold, when checked, what happens on breach. Mechanised as a test where possible.
- **Validation gate** — the pre-agreed condition that unlocks the next step, written *before*
  building.

`Killed` is a valid ADR status: the kill-test fired and the hypothesis was false. A corpus with
no `Killed` entry has kill-tests that are too lax. Scaffold a new record with `/adr-new`.
<!-- chrysa:standards:end -->
