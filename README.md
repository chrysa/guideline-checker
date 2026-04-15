# guideline-checker

[![CI](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/guideline-checker)](https://pypi.org/project/guideline-checker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![SonarCloud](https://sonarcloud.io/api/project_badges/measure?project=chrysa_guideline-checker&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=chrysa_guideline-checker)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

A CLI tool that reads GitHub Copilot instruction files (`.instructions.md`) from a project's `.github/instructions/` directory, validates the codebase against those rules, and generates a **detailed HTML compliance report**.

---

## Features

- **Instruction-driven**: rules are loaded from `.instructions.md` files (`applyTo` + content)
- **Pattern-based checking**: detects anti-patterns (print calls, bare excepts, console.log, etc.)
- **HTML report**: color-coded, grouped by rule file, with file paths and line numbers
- **JSON report**: machine-readable output for CI pipelines
- **CI-friendly**: exits with code 1 if violations are found (configurable threshold)
- **Pre-commit hook**: runs as a pre-commit hook on the entire project

---

## Installation

```bash
pip install guideline-checker
```

Or from source:

```bash
pip install -e '.[dev]'
```

---

## Usage

### CLI

```bash
# Check current directory against .github/instructions/
guideline-checker check

# Check a specific project root
guideline-checker check --root /path/to/project

# Output HTML report to a file
guideline-checker check --output report.html

# Use a custom instructions directory
guideline-checker check --instructions .github/instructions/

# Fail only if severity >= error (ignore warnings)
guideline-checker check --fail-on error
```

### Pre-commit hook

```yaml
- repo: https://github.com/chrysa/guideline-checker
  rev: v1.0.0
  hooks:
    - id: guideline-check
      args: [--fail-on, warning]
```

### GitHub Actions

```yaml
- name: Guideline check
  run: |
    pip install guideline-checker
    guideline-checker check --output guideline-report.html
```

---

## Report Format

The HTML report includes:

- **Summary**: total files checked, violations by severity
- **Per-rule sections**: rule description, `applyTo` pattern, violations found
- **Violation details**: file path, line number, matched pattern, suggestion
- **Color coding**: error / warning / info

---

## Architecture

```
guideline_checker/
    __init__.py             # package version
    cli.py                  # argparse entry point (guideline-checker command)
    loader.py               # load and parse .instructions.md files
    checker.py              # match files against rules by applyTo pattern
    hook.py                 # pre-commit hook entry point
    reporters/
        html.py             # HTML report generator
        json_reporter.py    # JSON report generator (CI artifact)
tests/
    test_checker.py         # checker engine tests
    test_cli.py             # CLI entry point tests
    test_hook.py            # hook entry point tests
    test_html_reporter.py   # HTML reporter tests
    test_json_reporter.py   # JSON reporter tests
    test_loader.py          # instruction loader tests
```

---

## Development

```bash
# Install dev dependencies
pip install -e '.[dev]'

# Run tests
pytest

# Lint
ruff check .
ruff format .

# Type check
mypy guideline_checker
```

---

## License

MIT -- see [LICENSE](LICENSE).
