# REQUIREMENTS — guideline-checker

> Generated. Requirements are reverse-engineered from README/CLAUDE/DECISIONS and the code tree.
> Status column: **IMPLEMENTED** only where a concrete module/test verifies it; otherwise
> **INFERENCE** or **UNKNOWN**. Evidence pointers are repo-relative.

## Functional requirements

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| REQ-FUN-001 | Discover rules from multiple markdown sources (`.github/instructions/*`, `copilot-instructions.md`, `CLAUDE.md`, `AGENTS.md`) | IMPLEMENTED | `loader.py`; `README.md` Features |
| REQ-FUN-002 | Load a structured YAML rule referential (`guidelines/<dimension>/*.yml`) | IMPLEMENTED | `guidelines.py`; `guidelines/`; `tests/test_guidelines.py` |
| REQ-FUN-003 | Detect violations deterministically by mechanism (pattern/presence/numeric/crossref/AST/scanner) | IMPLEMENTED | `core/detection/*`; `tests/test_core_detection_kinds.py`, `test_checker.py` |
| REQ-FUN-004 | Python AST detectors (`detect.ast`) | IMPLEMENTED | `core/detection/ast_python.py`; `tests/test_ast_python.py` (D-0005) |
| REQ-FUN-005 | JS/TS/JSX/TSX AST detectors via tree-sitter | IMPLEMENTED | `core/detection/ast_javascript.py`; `tests/test_ast_javascript.py` (D-0006) |
| REQ-FUN-006 | Entropy-based hardcoded-secret detection | IMPLEMENTED | `core/detection/scanners.py`; `guidelines/packs/security-strict.yml` (D-0008) |
| REQ-FUN-007 | Rule health classification (proven/armed/dead/advisory) | IMPLEMENTED | `core/health.py`; `tests/test_core_health.py` (D-0010) |
| REQ-FUN-008 | Baseline adoption — gate only new violations | IMPLEMENTED | `baseline.py`; `.guideline-baseline.json`; `tests/test_baseline.py` |
| REQ-FUN-009 | Reports in HTML / JSON / Markdown / SARIF | IMPLEMENTED | `reporters/*`; `tests/test_html_reporter.py`, `test_json_reporter.py` |
| REQ-FUN-010 | Versioned JSON result contract | IMPLEMENTED | `reporters/json_reporter.py`; `tests/test_json_contract.py` (D-0022) |
| REQ-FUN-011 | Local declarative autofix (`fix:` block) + remote drift PRs | IMPLEMENTED | `autofix.py`, `fixers.py`; `tests/test_autofix.py`, `test_fixers.py` (D-0017) |
| REQ-FUN-012 | External linter integration (ruff/mypy/eslint/biome) | IMPLEMENTED | `linters.py`; `tests/test_linters.py` |
| REQ-FUN-013 | Rule inheritance (`extends:`) and distributable packs (`include:`) | IMPLEMENTED | `guidelines.py`; `guidelines/packs/`; `tests/test_cross_reference.py` (D-0007, D-0018) |
| REQ-FUN-014 | CLI subcommands: init / check / fix / synthesize / web | IMPLEMENTED | `cli.py`, `init_cmd.py`; `tests/test_cli.py`, `test_init_cmd.py` |
| REQ-FUN-015 | Pre-commit hook (whole-project, always-run, fail-on error) | IMPLEMENTED | `hook.py`, `.pre-commit-hooks.yaml`; `tests/test_hook.py` |
| REQ-FUN-016 | GitHub Action wrapper | IMPLEMENTED | `action.yml` |
| REQ-FUN-017 | Diff mode (`--diff`) | IMPLEMENTED | `tests/test_diff_mode.py` |
| REQ-FUN-018 | Exclude globs / `.guidelineignore` | IMPLEMENTED | `.guidelineignore`; `tests/test_exclude.py` |
| REQ-FUN-019 | Web workshop: scan → health tiles → rules table → propose/replay/persist | IMPLEMENTED | `web/app.py`, `web/static/index.html`, `workshop/*` (D-0011, D-0015) |
| REQ-FUN-020 | LLM proposer backends (Claude CLI default, Ollama opt-in), propose-only | IMPLEMENTED | `workshop/proposer.py`; `tests/test_proposer*` (D-0012, D-0013) |
| REQ-FUN-021 | Sandbox replay produces proof before any write | IMPLEMENTED | `workshop/*`; `tests/test_sandbox*`, `test_generation_loop.py` |
| REQ-FUN-022 | Persist validated detector into `guidelines/*.yml` (dry-run diff) | IMPLEMENTED | `workshop/persist.py`; `tests/test_persist*` |
| REQ-FUN-023 | Fleet: multi-repo rule distribution + origin-side audit via `gh` | IMPLEMENTED | `fleet/*`; `tests/test_distribution.py`, `test_gh_client.py` (D-0009) |
| REQ-FUN-024 | Auto-derive proven detectors (derive seed/cache) | IMPLEMENTED | `core/derive/*`; `tests/test_derive_*` (D-0024) |
| REQ-FUN-025 | Central aggregation server (push model, file-backed store) | INFERENCE | `web/app.py`; `.env.example CENTRAL_STORE` (D-0003) — endpoints not fully traced here |

## Non-functional requirements

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| REQ-NFR-001 | Detection is deterministic and offline; LLM never judges | IMPLEMENTED | `CLAUDE.md`; D-0012/D-0016 |
| REQ-NFR-002 | Python 3.14 (CI matrix 3.12 + 3.14) | FACT | `CLAUDE.md`; `pyproject.toml` classifiers |
| REQ-NFR-003 | Ruff lint+format, mypy strict, pytest+cov ≥ 85% | FACT | `pyproject.toml addopts --cov-fail-under=85`; `CLAUDE.md` |
| REQ-NFR-004 | Not published on PyPI — install from source / ghcr | FACT | `README.md`, `CLAUDE.md` |
| REQ-NFR-005 | Version derived from git tags, never hardcoded | FACT | `pyproject.toml [tool.setuptools_scm]` (D-0019) |
| REQ-NFR-006 | Web auth pluggable (disabled/api_key/local/ldap/oidc) | IMPLEMENTED | `web/auth.py`; `.env.example` |
| REQ-NFR-007 | Web security headers | IMPLEMENTED | `web/security_headers.py` |
| REQ-NFR-008 | English-only code/comments/docs | FACT | `CLAUDE.md` Conventions |

## UNKNOWN / not verified here
- Performance targets for large trees (README cites `files_checked: 214` in an example only).
- Exact rule-count coverage vs. the referential (README lists a representative table, not exhaustive).
