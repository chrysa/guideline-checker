@AGENTS.md

# CLAUDE.md — guideline-checker

> **Claude Code**: also read `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` for code specifications.

## Documentation map

Generated root docs (reverse-engineered, evidence-tagged FACT/INFERENCE/UNKNOWN):
`ARCHITECTURE.md` (core/workshop/fleet split, pipeline, interfaces) · `REQUIREMENTS.md`
(REQ-* traceability matrix) · `CONSTRAINTS.md` (technical/process/host constraints) ·
`TESTING.md` (suite map + make commands) · `SECURITY.md` (secret review + owner triage items).
Authoritative sources remain: `README.md` (usage), `DECISIONS.md` (ADRs D-0001…D-0024),
`CONTRIBUTING.md`, `CHANGELOG.md` (auto-generated via git cliff).

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

Follow the /skills skill.
