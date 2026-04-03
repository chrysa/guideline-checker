# CLAUDE.md — guideline-checker

## Vision

Pre-commit hook and CLI tool for checking project compliance against GitHub Copilot
instruction rules (`.github/instructions/*.instructions.md`). Generates HTML and JSON
compliance reports with violation listings per rule file.

## Usage

### As a pre-commit hook

Add to your `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/chrysa/guideline-checker
  rev: v0.1.0
  hooks:
    - id: guideline-check
```

The hook runs `guideline-checker check --fail-on error` on the entire project.
It expects instruction files in `.github/instructions/`.

### As a CLI tool

```bash
pip install guideline-checker
guideline-checker check --root . --fail-on error
guideline-checker check --root . --json report.json --output report.html
```

### Integration with chrysa/pre-commit-tools

For projects using `chrysa/pre-commit-tools`, add both repos to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/chrysa/pre-commit-tools
  rev: <latest>
  hooks:
    - id: format-dockerfiles
    # ... other hooks ...

- repo: https://github.com/chrysa/guideline-checker
  rev: v0.1.0
  hooks:
    - id: guideline-check
```

## Structure

```
guideline_checker/
  checker.py      # Core check engine — runs rules against source files
  cli.py          # CLI entry point (argparse) — check subcommand
  hook.py         # Pre-commit hook entry point (delegates to cli.main)
  loader.py       # Instruction file loader and parser
  reporters/
    html.py       # HTML report generator (Jinja2)
    json_reporter.py  # JSON report output
.pre-commit-hooks.yaml  # Hook definition for pre-commit framework
tests/
  test_checker.py       # Core engine tests
  test_html_reporter.py # Reporter tests
  test_loader.py        # Loader tests
  test_hook.py          # Hook entry point tests
```

## Hook configuration

The `.pre-commit-hooks.yaml` defines:
- `id: guideline-check`
- `language: python` — installed in a virtualenv by pre-commit
- `pass_filenames: false` — runs on the whole project, not individual files
- `always_run: true` — runs even when no matching files are staged
- `args: [check, --fail-on, error]` — fails on first error-level violation

## Conventions

- Python 3.12+
- Ruff for linting and formatting
- Mypy strict mode
- Pytest + pytest-cov for tests
- All code, comments, issues, PRs, and docs in English

## Quickstart

```bash
pip install -e ".[dev]"
pytest
pre-commit run --all-files
```
