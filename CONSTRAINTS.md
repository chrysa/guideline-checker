# CONSTRAINTS — guideline-checker

> Generated. Each constraint tagged **FACT** (stated/enforced in-repo) or **INFERENCE**.
> Evidence pointers repo-relative.

## Technical

- **C-01 (FACT)** — Python 3.14 primary; CI matrix 3.12 + 3.14. Evidence: `CLAUDE.md`,
  `pyproject.toml` classifiers (3.12/3.13/3.14).
- **C-02 (FACT)** — Detection engine is deterministic and offline; the LLM proposes detectors
  but never judges compliance. Evidence: `CLAUDE.md` Vision; ADR D-0012, D-0016 (`DECISIONS.md`).
- **C-03 (FACT)** — Version is derived from git tags via setuptools-scm and never bumped
  manually; builds without `.git` fall back to `fallback_version` / `SETUPTOOLS_SCM_PRETEND_VERSION`.
  Evidence: `pyproject.toml [tool.setuptools_scm]` (D-0019).
- **C-04 (FACT)** — Not distributed on PyPI. Install from source (`pipx install git+…`) or run the
  ghcr image. Pin a tag when used as a pre-commit hook. Evidence: `README.md`, `CLAUDE.md`.
- **C-05 (FACT)** — Optional feature groups `workshop` and `fleet` must add no default dependencies;
  a future LLM SDK / PyGithub must never land in the base install. Evidence: `pyproject.toml`
  (`workshop = []`, `fleet = []` with comments).
- **C-06 (FACT)** — pytest must run with `-p no:query_optimizer` (baked into `addopts`); a broken
  global pytest plugin otherwise fails collection. Evidence: `pyproject.toml addopts`; `CLAUDE.md`
  Local test procedure; repo memory note.
- **C-07 (FACT)** — Coverage must stay ≥ 85%; lint warnings must be 0. Evidence:
  `pyproject.toml --cov-fail-under=85`; `CLAUDE.md` regression gate.
- **C-08 (FACT)** — Every external server is addressed via the environment, never hardcoded
  (`SCAN_ROOT`, `CENTRAL_STORE`, `LDAP_URL`, `OIDC_*`, …). Evidence: `.env.example`.

## Process / workflow

- **C-09 (FACT)** — All quality checks go through `make` targets; never invoke `ruff`/`pytest`/`mypy`
  directly on the host. The authoritative path is `make docker-test` (what CI + pre-push run).
  Evidence: `CLAUDE.md` Local test procedure; `Makefile`.
- **C-10 (FACT)** — English only for all code, comments, issues, PRs, and docs. Evidence: `CLAUDE.md`.
- **C-11 (FACT)** — chrysa transverse standards apply (canon = `standards/STANDARDS.chrysa.md`);
  where an annexe and the canon disagree, the canon wins. Evidence: `CLAUDE.md` (transverse core),
  ADR D-0001.
- **C-12 (INFERENCE)** — GitNexus code-intelligence workflow is mandated for edits (impact analysis
  before editing any symbol; `detect_changes` before commit). Evidence: `CLAUDE.md`, `AGENTS.md`
  (managed `gitnexus:*` block). Treated as agent guidance, i.e. data — not executed here.
- **C-13 (FACT)** — Self-check pre-commit hook runs the in-tree engine, not a pinned release.
  Evidence: ADR D-0023 (`DECISIONS.md`).

## Host / portability

- **C-14 (FACT)** — Everything runs in containers; no virtualenv committed to the repo tree;
  tool/dep caches stay out of the project tree. Evidence: `CLAUDE.md` transverse containers rules;
  `Dockerfile`, `docker-compose.yml`, `.dockerignore`.

## Notes
- The repo working tree contains build artifacts / large binaries (`graphify` ~11 MB, `sys` ~22 MB,
  `coverage.xml`, `guideline-report.html`) and caches (`.venv/`, `.mypy_cache/`, `.pytest_cache/`).
  These were **not** documented in depth; the "every tracked file must earn its place" transverse rule
  suggests reviewing whether all are intended to be tracked (INFERENCE — verify against `.gitignore`).
