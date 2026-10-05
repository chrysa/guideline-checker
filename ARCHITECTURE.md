# ARCHITECTURE — guideline-checker

> Generated documentation. Tags: **FACT** (verified in-repo), **INFERENCE** (reasoned from
> evidence), **UNKNOWN** (not determinable from the repo). Evidence pointers are file paths
> relative to the repo root. Instruction-shaped text found in source is treated as data, not
> as directives.

## 1. Purpose (FACT)

`guideline-checker` turns the coding rules already written for AI agents
(`.github/instructions/*.instructions.md`, `.github/copilot-instructions.md`, `CLAUDE.md`,
`AGENTS.md`) plus a structured YAML referential into an enforceable, **honest** lint pass.
It runs as a CLI, a pre-commit hook, or a GitHub Action, and ships a local web **workshop**
for authoring/validating rule detectors.

Evidence: `README.md`, `CLAUDE.md` (Vision), `action.yml`, `.pre-commit-hooks.yaml`.

"Honest" is a load-bearing concept (FACT, `CLAUDE.md`): every rule carries a **health** state —
`proven` (fires on real code), `armed` (detector present, no match), `dead` (YAML rule with no
detector = a real defect), `advisory` (a markdown bullet surfaced but never enforced). The tool
never shows green over a rule that can detect nothing.

## 2. Package layout — core / workshop / fleet split (FACT)

The `core/workshop/fleet` split is formalised in **ADR D-0024** (`DECISIONS.md`) and visible in
the package tree (`guideline_checker/`):

| Layer | Path | Responsibility (FACT from filenames / `CLAUDE.md` / ADRs) |
|-------|------|-----------------------------------------------------------|
| **core** | `guideline_checker/core/` | Deterministic engine, offline. `health.py` (rule health); `detection/` (`pattern.py`, `presence.py`, `numeric.py`, `crossref.py`, `kinds.py`, `scanners.py`, `scanner_source.py`, `ast_python.py`, `ast_javascript.py`); `derive/` (`seed.py`, `cache.py` — auto-derived proven detectors, D-0024). |
| **workshop** | `guideline_checker/workshop/` | Detector authoring loop. `proposer.py` (LLM/heuristic proposals, D-0012/D-0013), `interpret.py`, `persist.py` (write validated detector into `guidelines/*.yml`), `web_endpoints.py`. LLM **proposes only**; detection stays deterministic. |
| **fleet** | `guideline_checker/fleet/` | gh-backed multi-repo governance. `distribution.py`, `gh_client.py` (shells to `gh` CLI), `lifecycle.py`, `manifest.py`, `origin_audit.py` (origin-side distribution audit, D-0009). |
| **web** | `guideline_checker/web/` | FastAPI app (`app.py`), `auth.py` (api_key/local/ldap/oidc), `security_headers.py`, `mode.py`, `static/index.html` (single-page workshop UI). |
| **reporters** | `guideline_checker/reporters/` | Output formats: `html.py`, `json_reporter.py`, `markdown.py`, `sarif.py`, `synthesis_html.py`. |

Top-level modules (FACT, `guideline_checker/`): `checker.py` (core check engine, ~51 KB),
`cli.py` (entry point: init/check/fix/synthesize/web), `guidelines.py` (YAML referential loader),
`loader.py` (markdown instruction-file parser), `linters.py` (ruff/mypy/eslint/biome),
`autofix.py` + `fixers.py`, `baseline.py`, `config.py`, `workspace.py`, `init_cmd.py`,
`hook.py` (delegates to `cli.main`).

Optional installs are declared but currently dependency-free (FACT, `pyproject.toml`):
`workshop = []` and `fleet = []` are reserved so a future LLM SDK / PyGithub never lands in the
default install; `web` pulls FastAPI/uvicorn/ldap3/httpx/PyJWT; `e2e` pulls playwright.

## 3. Pipeline (INFERENCE from module names + README/CLAUDE)

1. **Load rules** — `loader.py` parses markdown instruction sources; `guidelines.py` loads the
   `guidelines/<dimension>/*.yml` referential (categories, languages, packs, data-ops, eventing).
2. **Detect** — `core/detection/*` runs deterministic detectors by *mechanism* (a finite generic
   taxonomy, ADR D-0020): pattern, presence, numeric-threshold (D-0021), cross-reference, AST
   (Python via stdlib `ast`, JS/TS via tree-sitter D-0005/D-0006), entropy scanners (D-0008).
3. **Judge health** — `core/health.py` classifies each rule proven/armed/dead/advisory (D-0010).
4. **Baseline** — `baseline.py` accepts current violations and gates only new ones.
5. **Report** — `reporters/*` emit HTML/JSON/Markdown/SARIF; JSON carries its own version (D-0022).
6. **Workshop loop** (opt-in) — detect → propose (`workshop/proposer.py`) → replay in sandbox for
   proof → validate → persist into YAML (`workshop/persist.py`).
7. **Fleet** (opt-in) — distribute/audit rules across repos via `gh` (`fleet/*`).

## 4. Interfaces (FACT)

- **CLI**: `guideline-checker` → `guideline_checker.cli:main` (`pyproject.toml [project.scripts]`).
  Subcommands: init/check/fix/synthesize/web (`CLAUDE.md`).
- **Pre-commit hook**: `id: guideline-check`, `language: python`, `pass_filenames: false`,
  `always_run: true`, `args: [check, --fail-on, error]` (`.pre-commit-hooks.yaml`, `CLAUDE.md`).
- **GitHub Action**: `action.yml` (inputs incl. `exclude`, `fail-on`; emits SARIF/Markdown/HTML).
- **Web API**: FastAPI `/api/scan|results|rules-health|propose|rules/detector` (`CLAUDE.md`,
  `guideline_checker/web/app.py`).

## 5. Configuration precedence (FACT, `README.md`)

CLI flag > environment variable > config file (`pyproject.toml [tool.guideline-checker]` or
`.guideline-checker.toml`) > built-in default. Unknown/wrong-type keys are ignored with a warning.

## 6. Constraints binding architecture (FACT — see CONSTRAINTS.md)

Deterministic + offline core; LLM proposes only; version derived from git tags (setuptools-scm,
D-0019); every external server addressed via environment (`.env.example`); pytest must run with
`-p no:query_optimizer` (`pyproject.toml addopts`, and repo memory).

## 7. UNKNOWNs

- Runtime data flow for the central aggregation server (D-0003 push model) beyond `CENTRAL_STORE`
  env is not fully traced here (UNKNOWN — inspect `web/app.py` + `fleet/` for the push endpoints).
- Exact tree-sitter grammar versions loaded at runtime (declared `>=0.23` in `pyproject.toml`).
