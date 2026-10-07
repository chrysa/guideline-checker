# TESTING — guideline-checker

> Generated. Commands are transcribed from `Makefile` / `CLAUDE.md` / `pyproject.toml` and are
> **not executed** by this documentation pass (docs-only). Tags: FACT / INFERENCE.

## Test suite (FACT)

- Framework: pytest + pytest-cov + pytest-mock (`pyproject.toml [project.optional-dependencies] dev`).
- Location: `tests/` (69 `test_*` files across `tests/` and `tests/e2e/`), plus `tests/fixtures/`.
- E2E: Playwright (`tests/e2e/`, `e2e` extra: `playwright`, `pytest-playwright`); excluded from the
  default run via `--ignore=tests/e2e` in `addopts`.
- Mandatory pytest flag: `-p no:query_optimizer` (baked into `addopts`) — see CONSTRAINTS C-06.
- Coverage gate: `--cov-fail-under=85` (`addopts`); `coverage.xml` is emitted with repo-relative
  paths for SonarCloud.

Representative coverage areas (FACT, from filenames): rule loading (`test_guidelines.py`,
`test_loader`-adjacent), detection kinds (`test_core_detection_kinds.py`, `test_checker.py`),
Python/JS AST (`test_ast_python.py`, `test_ast_javascript.py`), health (`test_core_health.py`),
baseline (`test_baseline.py`), reporters (`test_html_reporter.py`, `test_json_reporter.py`,
`test_json_contract.py`), CLI/hook (`test_cli.py`, `test_hook.py`, `test_init_cmd.py`), workshop
proposer/sandbox/persist (`test_proposer*`, `test_sandbox*`, `test_persist*`, `test_generation_loop.py`),
fleet distribution (`test_distribution.py`, `test_gh_client.py`), derive cache (`test_derive_*`),
linters (`test_linters.py`), diff/exclude modes, and the core/workshop/fleet boundary
(`test_core_boundary.py`, `test_conformance_independence.py`).

## Commands (FACT — from `CLAUDE.md` / `Makefile`)

```bash
make install-dev            # install with dev extras
make lint                   # ruff lint
make format-check           # ruff format --check
make typecheck              # mypy strict
make test                   # pytest on the host
make test-cov               # pytest with coverage report
make docker-test            # AUTHORITATIVE path — what CI + pre-push run
make pre-commit             # run all pre-commit hooks on every file
make e2e                    # Playwright e2e (install-e2e first)
make quality-gate-verify    # no-regression gate (SKIPs until a baseline is recorded)
make ci                     # lint + format-check + typecheck + docker-test (the PR gate)
```

Actionlint (workflow validation, FACT `CLAUDE.md`):
```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```

## Notes / verification status

- **INFERENCE**: The suite is expected green per repo memory ("make ci green — 909 tests, mypy").
  This pass did **not** run any tests (docs-only); treat the 909 figure as historical memory,
  not a re-verified count. The current file count is 69 `test_*` modules (FACT).
- `make test` / `make test-cov` run on the host; `make docker-test` is authoritative because a
  broken global pytest plugin on a host interpreter fails collection before tests run (FACT,
  `CLAUDE.md`).
- Mutation testing and a quality-gate check exist as GitHub workflows
  (`.github/workflows/mutation-testing.yml`, `quality-gate-check.yml`).
