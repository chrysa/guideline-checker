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
  cli.py                # CLI entry point — init/check/fix/synthesize/web
  hook.py               # Pre-commit hook entry point (delegates to cli.main)
  loader.py             # Instruction file loader/parser (markdown sources)
  guidelines.py         # Structured YAML rule referential loader (guidelines/<dimension>/*.yml)
  autofix.py            # Local declarative autofix (fix: block); fixers.py = remote drift PRs
  linters.py            # External linter integration (ruff / mypy / eslint / biome)
  reporters/            # html.py, synthesis_html.py, json_reporter.py, markdown.py, sarif.py
  web/
    app.py              # FastAPI app — dashboard + /api/scan|results|rules-health|propose|rules/detector
    static/index.html   # Single-page workshop UI (bundled via package-data)
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
make quality-gate-verify   # SKIPs until a baseline is recorded — it verifies nothing today
```

### Regression gate (before every PR)
```bash
make ci
# Runs lint + format-check + typecheck + docker-test.
# Coverage must stay >= 85%. Lint warnings must be 0.
```

`make test` and `make test-cov` run pytest on the host. The authoritative path is
`make docker-test`, which is what CI and the pre-push hook run — a host interpreter
carrying a broken global pytest plugin fails collection before any test runs.

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

## Normative annexes

This file is the **only** artifact inlined into consumer repos. The annexes below are
**equally normative** — they detail rules stated here in short form. They are not inlined;
read them at
`https://github.com/chrysa/shared-standards/blob/main/standards/annexes/`.
Where an annexe and this file disagree, **this file wins**.

| Annexe                    | Scope                                                          |
| ------------------------- | -------------------------------------------------------------- |
| `FRONTEND.md`             | TypeScript config & rules · React layering · frontend architecture · frontend tests |
| `ARCHITECTURE-DDD.md`     | project profiles · DDD levels · layers & aggregates · Python & C#/.NET structure |
| `AGENTIC-CAPABILITIES.md` | agent actions: manifests, risk R0–R5, sandboxing, audit trail   |
| `PROJECT-DECOUPLING.md`   | inter-project contracts, forbidden linkages, degradation        |
| `CONTAINERS-K3S.md`       | reference stage shape · container responsibility · k3s workload baseline |
| `TESTING.md`              | common test levels and rules across languages                   |
| `CI-CD.md`                | pipeline architecture · action pinning · least privilege · cost · what the gate proves |
| `GOVERNANCE.md`           | rule identity, maturity ladder, enforcement rollout, sources of truth |

**Source of truth:** the canon lives in this repo. Notion is a governance and decision view
of the standards corpus, not its authority (`GOVERNANCE.md` GV-000). `chrysa/standards` is
deprecated and archived — nothing is added to it, nothing reads from it.

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
| Auth             | Cluster SSO (OIDC) → external OAuth → local (bcrypt) · MFA-capable |
| i18n             | react-i18next + fastapi-babel · FR + EN from V1                 |
| Monorepo         | Turborepo + pnpm workspaces                                     |
| Versioning       | [GitVersion](https://gitversion.net/) (semantic auto — never bump manually) |
| Quality CI       | SonarCloud (0 hotspot · rating A)                               |
| Linting          | Ruff + Mypy (Python) · ESLint (TS)                             |
| Pre-commit       | detect-secrets + ruff + mypy + commitlint                      |
| Error handling   | withErrorHandling() → auto GitHub Issue on failure             |
| Hosting          | Kimsufi · Docker Compose (local) · Nginx · Certbot · Tailscale  |
| Monitoring       | Sentry + Uptime Kuma (self-hosted)                            |
| Agents           | Claude API (primary) · Ollama (fallback)                       |
| Orchestration    | LangGraph (stateful) · PydanticAI (structured outputs)         |
| Registry         | GHCR private `ghcr.io/chrysa/{repo}` — never public            |
| Docs             | MkDocs → GitHub Pages (`pages.yml`) · ADRs in `docs/adr/`       |
| Changelog        | [git-cliff](https://git-cliff.org/) (`cliff.toml`) · Keep a Changelog |

## Non-negotiable conventions

The fleet-wide constitution lives in
[`chrysa/shared-standards/CLAUDE.md`](https://github.com/chrysa/shared-standards/blob/main/CLAUDE.md)
— read it there; it is not duplicated here (v2 redesign, D-0024). The deltas below are
guideline-checker-specific and take precedence where they conflict with the shared standard.

### Repo-specific deltas

_guideline-checker currently overrides nothing in the shared constitution: every bullet that
previously lived here was a verbatim copy of it. This repo's own rules live in the `## Vision`,
`## Usage`, `## Structure`, `## Conventions`, and `## Local test procedure` sections above._

## Quality gates

- Test coverage **>= 85%** by default. A repo may override upward, never below 80%.
- Lint warnings: **0**. Mypy clean. SonarCloud rating **A**, 0 security hotspot.
- Max function lines 50 · max file lines 500 · cyclomatic complexity heuristic <= 10.
- **Performance and cost budgets are declared per profile and enforced.** Frontend bundle,
  Docker image size, startup time, memory, CPU, latency, throughput, storage and log volume
  each carry a budget; AI paths additionally budget tokens, cost, latency, concurrency and
  cache. CI measures them and **blocks significant regressions** (info → warning → error); an
  overrun carries a justification, an impact measurement and a reduction plan — never a silent
  pass. Detail: annexe `CI-CD.md` CI-053.

## Design system

Every human-facing surface is built from a shared design system — no ad-hoc style values in
components. This complements *dark mode + WCAG 2.1 AA* and the `ui-ux` skill.

- **Design tokens are the single source of style** — colours, typography, spacing, radii,
  shadows, z-index live as tokens (JSON/CSS vars) consumed by code. **No** hardcoded style
  literals in components (mirrors *no hardcoded constants*).
- **Versioned brand kit** — primary/secondary/semantic palette, **≤ 2 type families**, logo
  (variants + clear space), one icon set. Defined and versioned, not per-repo reinvented.
- **Living component library** — reusable components with documented states and variants
  (Storybook or equivalent); one canonical implementation per component.
- **Systematic spacing scale & grid** — spacing on a fixed scale (4/8 px base), shared grid
  and breakpoints; no arbitrary margins.
- **Defined type hierarchy** — explicit type scale (size, weight, line-height, tracking) with
  named roles (`display/title/body/caption`), never ad-hoc sizes.
- **Systematic interaction states & feedback** — every interactive element exposes
  hover/focus/active/disabled; every action gives visible feedback (< 100 ms); visible keyboard
  focus is mandatory.
- **Consistent UX writing** — voice-and-tone guide; error messages say what to do (no raw
  codes); action-oriented labels and CTAs; terminology aligned to the domain glossary.
- **Standardised motion** — tokenised durations and easing (e.g. 150/250 ms); animation is
  functional (state transition, feedback), never gratuitous; honours `prefers-reduced-motion`.
- **Mobile-first responsive** — mobile-first design, breakpoints from tokens, touch targets
  **≥ 44 px**, no fixed widths.
- **Design ↔ dev handoff contract** — design ships exported tokens, component specs (measures,
  states, behaviours) and edge cases; dev consumes the tokens, never redefines the values.

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
- **Modular Makefiles — 500 lines max, split by domain.** No hand-maintained Makefile exceeds
  **500 lines** (the same file gate as code). Approaching the limit, it is split into thematic
  files under `make/` (`make/common.mk` for shared variables/functions, then `docker.mk`,
  `test.mk`, `quality.mk`, `k8s.mk`, `docs.mk`… as the repo needs), loaded explicitly from the
  root Makefile with `include` / `-include`. The **root Makefile stays an entry point and an
  orchestrator**: it exposes the main commands, loads the thematic files, and serves the global
  `make help`. A target exists **in exactly one file** — duplicates, near-identical variants and
  copy-paste between thematic files are forbidden (*no code duplication* applies to Make too).
  Inclusion is acyclic: a thematic file never includes back into its parent. Target names stay
  predictable and grouped by domain (`test-unit`, `docker-build`, `k8s-deploy`), every public
  target is documented in `make help` from its `## Description`, and any long or business-logic
  recipe moves to a **versioned, testable script** — the Makefile is a command surface, not an
  application language.

## Container-runtime policy

A project runs **only in a container** unless its nature genuinely forbids it. Convenience, "easier
on the host", or "it's just a script" are **not** exemptions — when in doubt, classify `container`.
Every repo carries a `runtime:` field in `repos.yml`, machine-checked by `audit-docker-compliance.sh`:

- `container` — runs as a service. Provides Dockerfile(s) + `docker-compose*` + `HEALTHCHECK` +
  `docker-up`/`docker-down`/`docker-test` targets.
- `exempt:lib` — distributed/imported (library, plugin, pre-commit hook, GitHub Action, CLI). Runs
  in the consumer's environment; provides a `docker-test` target (CI runs the suite in a container).
- `exempt:config` — no executable runtime (config, knowledge base, deploy manifests). Nothing to run.
- `exempt:native` — bound to a host OS, device, cloud platform, or editor (desktop integration,
  hardware, Apps Script, VS Code extension, infra/Helm). Optional `Dockerfile.test` for CI.
- `pending` — pre-code scaffold; flips to `container` at first code.

## Release & changelog config (canonical)

- **Versioning** is GitVersion (`GitVersion.yml`, flat `mode: ContinuousDeployment`) — never bump
  manually. Legacy v5 schemas (`GitHubFlow`, no top-level `mode:`) are incompatible and must be
  **replaced**, not version-bumped.
- **Changelog** is generated by [git-cliff](https://git-cliff.org/) (`cliff.toml`), Keep a
  Changelog format, from Conventional Commits — a non-conventional message is silently
  absent from the changelog, which is why the commit convention is a commit-gate hook.
- `GitVersion.yml` and `cliff.toml` are **canonical files** with a single source of truth in
  shared-standards (repo root + byte-identical `templates/` copy). A `repo: local` pre-commit hook
  (`gitversion-canonical-drift`, `cliff-canonical-drift`) blocks drift; `audit-canonical-conformance.sh`
  audits the fleet.
- **Docs** live in `docs/` (MkDocs), deployed to GitHub Pages via `pages.yml`. `README.md` reflects
  the actual current state and is updated on each release.
- **Registry** — application images publish to **private GHCR** (`ghcr.io/chrysa/{repo}`, tags mirror
  the git tag + `:latest`); CI authenticates with the workflow `GITHUB_TOKEN` (or least-privilege
  `packages:write`), never a plaintext PAT. Distributable libraries publish to public PyPI via
  Trusted Publishing (OIDC), never a token in plaintext.

## GitHub Actions (reuse first · custom actions centralised · thin workflows)

CI is assembled from **existing actions**, not written. A workflow is glue — checkout,
setup, invoke the repo's own gate (`pre-commit`, `make ci`) — and every line of logic it
carries is a line that lives in the wrong repo. A pipeline has one job: **tell the truth
about the code, fast, without becoming a codebase of its own**. Full rules, with ids and a
review checklist: annexe [`CI-CD.md`](https://github.com/chrysa/shared-standards/blob/main/standards/annexes/CI-CD.md)
(`CI-000`…`CI-053`) — pipeline architecture, supply-chain pinning, least privilege,
cost/latency, what the gate must prove, feedback.

Five of its rules are load-bearing enough to state here:

- **Every repo runs CI, and every deployable product ships CD** (`CI-006`, `CI-047`). There is
  no repository without a pipeline: CI runs the repo's own gate (`make ci` / `pre-commit`) on
  every push and PR, scaled to the `runtime:` tier. A deployable product (`runtime: container`)
  or a published library delivers through an **automated, environment-gated** pipeline — never a
  laptop deploy — and what CD ships **announces its version** in production (the `/version`
  endpoint + admin surface already required below).
- **A red check means the code is wrong** (`CI-040`). A gate that fails because a repo is not
  onboarded, a tool is missing or billing lapsed trains everyone to ignore red — and the next
  real failure is ignored too. Fix it or remove it the day it appears.
- **A skipped job reports as skipped, never as passed** (`CI-032`). Path filters may skip work;
  they must never turn a required check green without running it. A tick that means "not
  executed" destroys trust in the whole pipeline.
- **Build once, promote the artefact** (`CI-046`). The image digest that was tested is the one
  deployed; rebuilding per environment means production runs something no test ever saw.
- **Every job declares `timeout-minutes:` and every PR workflow a concurrency group**
  (`CI-030`, `CI-031`) — cancelling superseded PR runs, and explicitly **not** cancelling
  deployments.

- **Reuse before writing — always.** The first choice is a **maintained public action**
  (`actions/checkout`, `actions/setup-python`, `actions/setup-node`, `astral-sh/setup-uv`,
  `docker/build-push-action`, `SonarSource/*`, `pypa/gh-action-pypi-publish`, …).
  Re-implementing in a `run:` block something a maintained action already does — caching,
  toolchain setup, publishing, artifact upload — is a defect. Preferring a hand-rolled
  script because "it's shorter" is not a reason.
- **The only home for chrysa-specific actions is `chrysa/github-actions`.** When no public
  action fits, the behaviour becomes a composite action / reusable workflow in that repo
  (`python-setup`, `ruff-check`, `run-tests`, `sonar-scan`, `publish-python-package`, …)
  and consumers reference it: `uses: chrysa/github-actions/<action>@<rev>`. Reusable
  workflow templates live in `shared-standards/workflows/` and are distributed, never
  hand-forked.
- **Repo-local actions are forbidden by default.** No `.github/actions/**` composite in a
  product repo, no inline bash beyond glue, no `scripts/ci-*.sh` that exists only to be
  called by a workflow. The **second occurrence of the same CI logic anywhere in the fleet
  is an extraction order**, not a copy: it moves to `chrysa/github-actions` and both repos
  consume it. A repo-local action is tolerated only as a short-lived spike, with an issue
  tracking its migration.
- **Keep the code minimal.** A job step is a `uses:` or a one-line `run:`. A `run:` block
  past ~15 lines, or any conditional/parsing/retry logic, does not belong in YAML — it
  becomes a tested entrypoint inside the action repo (Python preferred, testable), not a
  heredoc. Duplicated near-identical jobs collapse into a `strategy.matrix`; shared setup
  collapses into a composite action. Workflow YAML is not a programming language and is
  not covered by any test.
- **Pinning & permissions.** Third-party actions are pinned by **commit SHA** (with the
  version in a trailing comment); `chrysa/github-actions` and `actions/*` by tag.
  Workflows declare least-privilege `permissions:` (read by default, `packages:write` /
  `contents:write` only on the job that needs it), never a plaintext PAT where the
  workflow `GITHUB_TOKEN` or OIDC works. Dependabot keeps the `github-actions` ecosystem
  up to date.
- **Secrets are passed explicitly — `secrets: inherit` is banned.** A reusable workflow
  receives only the secrets it actually uses, named one by one under `secrets:`
  (`secrets: {SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}}`). `secrets: inherit` hands the
  callee the caller's entire secret store, so a compromised or careless step reaches
  credentials it was never meant to see, and no one can tell from the call site which
  secrets a workflow consumes. The same rule applies to steps: scope `env:` to the step
  that needs the value, never to the job or the workflow when a single step uses it. A
  workflow whose secret list is not readable at the call site is a defect.

## Pre-commit & git hooks (native, via pre-commit.com — never wrapped in make)

The enforcement engine is **[pre-commit](https://pre-commit.com/)** itself, configured
in `.pre-commit-config.yaml`. pre-commit is the authoritative runner; `make lint` /
`make pre-commit` may exist as thin convenience aliases, but a hook that only runs
through `make` is a defect — every hook MUST be runnable via `pre-commit run` directly,
and CI invokes `pre-commit`, not `make`.

- **Every git hook goes through the framework — no hand-rolled hooks.** A repo's hooks are
  declared as pre-commit hook ids in `.pre-commit-config.yaml` and installed by
  `pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push`.
  Hand-written scripts committed under `.git/hooks/` or a repo-local `hooks/` wired via
  `core.hooksPath`, alternative runners (**husky**, **lefthook**, **overcommit**, npm
  `prepare` hook installers), and checks reachable only through `make` or a bespoke
  `scripts/*.sh` are **forbidden**: a gate that is not a pre-commit hook id is not
  discoverable, not pinned, not skippable per-hook (`SKIP=`), and not runnable in CI the
  same way. A repo-specific check is a `repo: local` hook in the config (or a hook published
  by `chrysa/pre-commit-tools`), not a script bolted onto git. The single sanctioned
  exception is the host-global pre-push (`dotfiles/git-hooks-global/pre-push`), which is
  machine-level, not repo-level, and itself only invokes `pre-commit`.
- **The gate is host-native — no strong coupling to the project's containers.** pre-commit
  runs with only `pre-commit` installed on the host (via `pipx`/`uv`, outside any repo); it
  provisions each hook's isolated environment itself (`~/.cache/pre-commit`), so a commit
  needs **no project image and no running container**. Local hooks are `language: system` /
  `python` (or another native language) invoking **host** tools — **never** `docker compose
  run`, and `language: docker` / `language: docker_image` is **forbidden**. A check that
  genuinely needs the project image (Django settings, a DB, a compiled tool) **degrades gracefully on the host**:
  it probes for the tool and skips with a message when absent
  (`command -v <tool> >/dev/null 2>&1 && <run> || echo 'skipping — runs in CI/Docker'`),
  it does **not** spin up a container. Container-side enforcement is CI's job; locally the
  gate is best-effort and never blocks on the Docker daemon being up. This does not
  contradict the container-runtime policy — the *application* runs in a container; the
  *commit gate*, like git, is a host tool.
- **Two stages, two scopes — do not mix them:**
  - **commit stage** (`pre-commit run`, default): auto-fixers + fast lints —
    `ruff`, `end-of-file-fixer`, `trailing-whitespace`, `detect-secrets`/`gitleaks`,
    `conventional-pre-commit` (commit-msg), `no-commit-to-branch --branch main`.
    These **mutate** the tree, so they only ever run over the staged/committed diff.
  - **pre-push stage** (`pre-commit run --hook-stage pre-push`): only hooks tagged
    `stages: [pre-push]` (e.g. `regression-gate` from `chrysa/pre-commit-tools`), run
    **natively over the pushed commit range** (`--from-ref <remote>` `--to-ref <local>`).
    A push **verifies, it never mutates** the tree.
- **Forbidden at push time:** `make lint`, and `pre-commit run --all-files`. Running the
  full tree at push re-executes commit-stage **auto-fixers** on unrelated files, mutates
  them, exits non-zero, and **rejects the push over a pre-existing defect in a file you
  never touched**. `--all-files` belongs to CI (where a mutation surfaces as a diff) and
  to a deliberate local audit — never to the push gate.
- **The global pre-push hook** (`dotfiles/git-hooks-global/pre-push`) mirrors pre-commit's
  own installed pre-push hook: it runs the `pre-push` stage over the range only, then the
  SonarCloud quality gate. No `make`, no `--all-files`, no tree mutation.
- **The shared hook package is Docker-free by construction.** `chrysa/pre-commit-tools`
  — the hook-decentralisation package the whole fleet consumes — publishes every hook as
  `language: python` (or another native pre-commit language) with its dependencies declared
  in the hook definition. **Forbidden in that package:** `language: docker`,
  `language: docker_image`, and any `docker` / `docker compose` invocation inside a hook
  entrypoint. A published hook MUST run identically on a host where Docker is not installed
  at all; if a check cannot work without the daemon, it is a CI job, not a hook. This keeps
  the fleet gate installable with a single `pipx install pre-commit` and immune to the
  daemon being down.
- Hooks are **pinned by `rev`**; shared hooks come from `chrysa/pre-commit-tools`.
  `detect-secrets`/`gitleaks` respect the repo's secret allowlist.
- **Hook logic is centralised in `chrysa/pre-commit-tools` — `repo: local` is glue only.**
  Every gate is declared in `.pre-commit-config.yaml`, and any hook carrying real logic
  is published as a versioned hook id in `chrysa/pre-commit-tools`, consumed by `rev`.
  `repo: local` is reserved for genuinely repo-specific glue (a path check tied to this
  repo's layout) and stays a few lines; it is never a home for a check other repos could
  want. As with GitHub Actions, the **second occurrence of the same hook anywhere in the
  fleet is an extraction order**, not a copy: it moves to `chrysa/pre-commit-tools` and
  both repos consume it from there. A hook duplicated across repos cannot be fixed once,
  drifts silently, and is the reason a fleet-wide rule change costs sixty pull requests
  instead of one.

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
- `accessibility` — per-disability-category contract + testable DoD (any surface, incl. public micro-sites)

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

## Session lifecycle (primer + memory + hindsight)

Every repo ships a session lifecycle so an AI agent keeps context across sessions. Bootstrap with
`make memory-init`; scripts live in `shared-standards/scripts/`.

- `primer.md` (committed) — current state, what to do NOW; read **before** `CLAUDE.md`.
- `.claude/memory/session.md` — volatile session notes, **not** committed (reset each session).
- `.claude/memory/decisions.md`, `known-issues.md`, `progress.md` (append-only history) — committed.
- **Session start**: `make prepare` (`/prepare`) — shows primer + git context + open PRs.
- **Session end**: `make hindsight` (`/hindsight`) — updates `primer.md` + `progress.md`, clears
  `session.md`, optional Obsidian export (`OBSIDIAN=<path>`).

## Compliance targets

The fleet is held to two external compliance frameworks. Neither is a separate corpus — each
is operationalised by rules already in this canon; declaring the target names the obligation
those rules must satisfy, and certification is a governance program on top, not a code change.

- **GDPR / RGPD — by construction.** Every product that touches personal data records its
  lawful basis and purpose, minimises and time-bounds what it stores, keeps PII out of logs
  and test data, and supports export / rectification / erasure by a documented command. This
  is *per-person data implies a user account* and *portable personalisation data* applied to a
  legal obligation. Detail: annexe `GOVERNANCE.md` GV-040.
- **ISO/IEC 27001 — the security baseline.** Information security is a governed, documented
  ISMS, not ad-hoc practice. Access control, cryptography, logging and audit, operations and
  change control, supplier security, and incident management each map onto an existing canon
  rule (cluster SSO & session security, secrets handling, observability & audit trail, CI
  gates & protected `main`, project decoupling & supply-chain pinning, typed/contained errors),
  so conformance is reached by satisfying those — not a parallel checklist. The organizational
  artefacts ISO 27001 also demands (ISMS scope, risk assessment & treatment, Statement of
  Applicability, internal audit) are a versioned governance backlog under `docs/`. Detail:
  annexe `GOVERNANCE.md` GV-041.

## Governance — strategic pillars & ADR format

Five non-negotiables hold across every chrysa project, whatever the stack. Breaking one
requires an ADR with a kill-test, not a shrug.

1. **LLM-provider independence** — no vendor SDK in business code; inference goes through a
   local port with **≥2 real, tested adapters** (e.g. Claude + a local model). A prompt that
   only works on one vendor is a bug, not a feature. **"Local model" means a model running on
   the machine or self-hosted** — an interpreter/weights the owner runs (Ollama, llama.cpp, a
   vLLM/TGI server on chrysa infrastructure), never a third-party hosted API dressed up as
   "local". The independence is only proven when one of the tested adapters needs no external
   provider to answer. **Every LLM call — internal or external — goes through the `chrysa-LLM`
   gateway**, never a vendor SDK or raw provider endpoint called directly from a product's
   business code. `chrysa-LLM` *is* the local port of this pillar made concrete across the
   fleet: it owns provider selection and the ≥2 tested adapters, and it is the one place where
   routing, fallback, prompt/model/version pinning, evaluation, cost and token budgets, caching,
   rate limiting and observability live (satisfying the *AI feature is evaluated* and *agent
   actions are governed* obligations once, not per repo). A product calls it as a **versioned
   contract** through a thin local adapter (*projects talk through versioned contracts only*)
   and degrades to a documented no-AI / fallback mode when it is unreachable — it never reaches
   a model by any other path. A direct call to Claude, OpenAI, Ollama, or any inference endpoint
   that bypasses `chrysa-LLM` is a defect, not a shortcut; the single documented exception is
   `chrysa-LLM` itself, which owns the real adapters. Products built *on top of* the gateway —
   e.g. `ai-aggregator`, a showcase/front consuming `chrysa-LLM` — are consumers of this
   contract, not alternative gateways: they route through `chrysa-LLM` like everything else and
   never re-implement provider access. This is the transport-level application of *no code
   duplication* and *external servers addressed through the environment*: the gateway's endpoint
   arrives by env, and the adapters exist once, there.
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
