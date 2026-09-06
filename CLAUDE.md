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
# chrysa — Transverse Standards (core)

> The **slim always-on core**. The canonical, tool-agnostic source of truth is `standards/STANDARDS.chrysa.md`; the normative annexes live under `standards/annexes/`. Each rule below is a one-line pointer — its full text lives in the per-domain file named beside the heading (`standards/rules/<domain>.md`), read on demand.

**Where an annexe and the canon disagree, the canon wins.**

### Governance, language & compliance · `standards/rules/governance.md`
- Normative annexes
- Language
- Compliance targets
- Governance — strategic pillars & ADR format

### Cross-cutting stack · `standards/rules/stack.md`
- Cross-cutting stack (settled ADRs — do not relitigate)

### SCM — branches, commits & pull requests · `standards/rules/scm.md`
- Commits
- Branches
- Branch model — `main` is production, `develop` is the workspace
- Merge
- One PR per issue
- Issues and PRs are type-driven

### Architecture, decoupling & portability · `standards/rules/architecture.md`
- Repo provenance — every code repo depends on `project-init`
- Every repo declares its profile and DDD level
- Projects talk through versioned contracts only
- Everything is machine-agnostic and portable — no rule, repo, or script is bound to one machine
- Every external server the service talks to is addressed through the environment — never hardcoded
- Every tracked file and folder must earn its place — a repo holds only what is useful to it now
- The repository architecture is legible to an agent — optimised for Claude, not only for humans
- Deferred work is a governed job, not a fire-and-forget

### Testing · `standards/rules/testing.md`
- Tests: pytest only
- Frontend tests: Vitest + Testing Library + MSW — from the scaffold, not later

### Frontend & web semantics · `standards/rules/frontend.md`
- TypeScript is strict by contract
- The JS/TS package manager is `pnpm` — `npm` and `yarn` are forbidden
- React is a presentation layer, not the domain
- The frontend says when the backend is unreachable or unstable
- The frontend is reactive and real-time by default
- UI state survives reload & focus
- Everything is semantic — the markup, the data, and the URLs
- URL-addressable frontend navigation — mandatory

### APIs, contracts & real-time · `standards/rules/api.md`
- A real-time backend has channel contracts and never blocks
- APIs, SDKs & public contracts follow the `STD-API-001` contract

### Accessibility · `standards/rules/accessibility.md`
- Dark mode
- Every site is usable by the majority of disabilities — not only the screen-reader case

### Documentation & session state · `standards/rules/docs.md`
- Notion logging
- Documentation and Notion are maintained in lockstep with the code — a change that leaves them stale is unfinished
- Session lifecycle (primer + memory + hindsight)

### AI agents & features · `standards/rules/agents.md`
- Agent actions are governed
- An AI feature is evaluated, not just shipped
- An agent writes only where the owner owns

### Security, identity & sessions · `standards/rules/security.md`
- Per-person data implies a user account — no exceptions dressed up as simplicity
- Identity goes through the cluster SSO first
- Rights are resolved against the common directory (LDAP), never re-declared per service
- A session is secured and it expires
- Every form is a hostile input surface — validate on the server, always
- Security scanning is a gate, not an afterthought — it runs in pre-commit and in CI

### Code quality & anti-patterns · `standards/rules/code-quality.md`
- No hardcoded constants
- No literal HTTP status codes — use the constants the framework already ships
- No code duplication — the second occurrence is an extraction order
- Raised errors are typed
- Failures are contained, and observable
- Prefer a lookup table to a state machine
- Decompose into small, independently unit-testable methods
- Code is read far more often than it is written — optimise for the reader, and standardise the form
- Avoid lambdas and anonymous constructs — a named function is the default
- Basic optimisations and known anti-patterns are caught in review and in CI
- A cache is a correctness contract, not a sprinkle of speed
- Quality gates
- Error handling pattern (all automations)

### Backend Python · `standards/rules/backend-python.md`
- Python packaging — `pyproject.toml` is the single source of truth
- Python is written object-oriented, one class per file
- Import the item, not the module — `from x import y; y()`
- Functions and methods are called with named arguments — positional call sites are the exception, not the rule

### Data, persistence & migrations · `standards/rules/data.md`
- Data, persistence & migrations follow the `STD-DATA-001` contract

### Observability & operations · `standards/rules/observability.md`
- Observability & production readiness follow the `STD-OPS-001` contract
- The container is versioned separately from the application it hosts, and an admin can see what is actually deployed
- Observability — error-tracking → GitHub issues (norm)

### Containers & compose · `standards/rules/containers.md`
- Everything runs in a container — the only exception is the slice of a repo genuinely bound to the host OS
- External dependencies are installed in containers, never on the host
- No virtualenv in a repo — ever
- Tool caches & deps never touch the project tree
- Dockerfiles are multi-stage, with a `production` and a `dev` stage — mandatory
- App containers ship the app only — the platform layer is the owner's responsibility
- Only a publicly useful port is published — everything else stays on the container network
- A compose file is minimal — declare only what the stack needs, default the rest
- Dev stage must hot-reload
- Local dev runs the code in-container, live, in debug mode — never the production server
- Default to dev mode when starting an app locally — any other mode only when explicitly asked
- `.dockerignore` mandatory & exhaustive
- Container-runtime policy

### Product surfaces · `standards/rules/product.md`
- Setup wizard & config panel
- A game is DRM-free and fully playable solo offline
- Every product that is operated ships a management backoffice
- If a user can supply a file, the product accepts an upload
- A floating assistant where it earns its place — never as decoration

### Design system · `standards/rules/design.md`
- Design system

### Developer loop & tooling · `standards/rules/dev-loop.md`
- Makefile targets
- Shared skills (load on demand from shared-standards/.claude/skills/)

### CI/CD, pre-commit & release · `standards/rules/ci-cd.md`
- Release & changelog config (canonical)
- GitHub Actions (reuse first · custom actions centralised · thin workflows)
- Pre-commit & git hooks (native, via pre-commit.com — never wrapped in make)
<!-- chrysa:standards:end -->
