---
# guideline-checker — Copilot Instructions

## MANDATORY: Read Instructions Before Any Task

Before working on this project, check relevant instruction files in `.github/instructions/`.

| File | Applies to |
|---|---|
| `.github/instructions/python_guidelines.instructions.md` | `**/*.py` |
| `.github/instructions/typing.instructions.md` | `**/*.py` |
| `.github/instructions/ruff_compliance.instructions.md` | `**/*.py` |
| `.github/instructions/testing.instructions.md` | `tests/**/*.py` |

---

## Project Overview

`guideline-checker` is a **CLI tool** that:
1. Reads `.instructions.md` files from `.github/instructions/`
2. Validates codebase files against rules defined in those instructions
3. Generates a **color-coded HTML compliance report**
4. Can run in CI or as a pre-commit hook

## Architecture

```
guideline_checker/
    cli.py          # argparse entry point -- main() accepts argv
    loader.py       # parse .instructions.md files (applyTo, description, rules)
    checker.py      # match files to instructions, evaluate rules, produce Violation objects
    reporters/
        html.py     # HTML report (no external deps, pure stdlib + string templates)
        json_reporter.py  # JSON report for CI artifacts
tests/
    test_loader.py
    test_checker.py
    test_html_reporter.py
```

## Key Constraints

- **Python 3.12+** (target 3.14, retro-compat to 3.12)
- **`from __future__ import annotations`** in every Python file
- **Typed**: ALL public functions must have complete type annotations
- **No external runtime deps** beyond `pyyaml` and `jinja2` (optional)
- **`argv` pattern**: every `main()` accepts `argv: list[str] | None = None`
- **ruff** — zero-tolerance (config in `pyproject.toml`)
- **pytest** — 100% passing tests required before commit
- **Single return point** per method/function

## Data Flow

```
InstructionFile (loader.py)
  └─► RuleResult (checker.py)
        ├── instruction: InstructionFile
        ├── violations: list[Violation]
        └── files_checked: int

Violation
  ├── file: Path
  ├── line_number: int
  ├── line_content: str
  ├── rule: str
  └── severity: "error" | "warning" | "info"
```

## CI/CD

- **GitHub Actions**: `.github/workflows/ci.yml` — ruff, mypy, test, sonar, release
- **Matrix**: Python 3.12, 3.13, 3.14
- **SonarCloud**: configured via `SONAR_TOKEN` and `SONAR_PROJECT_KEY` secrets/vars
- **Versioning**: GitVersion (`GitVersion.yml`) + git-cliff (`cliff.toml`)

## Pre-commit hook

```yaml
- repo: https://github.com/chrysa/guideline-checker
  rev: v1.0.0
  hooks:
    - id: guideline-check
```
