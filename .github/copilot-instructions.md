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


## Quality Thresholds

- Max function length: 50 lines when practical.
- Max file length: 500 lines when practical.
- Max cyclomatic complexity: 10.
- Lint warnings target: 0.

## Regression Prevention (NON-NEGOTIABLE)

Before marking **any** task or sub-task as done, the agent MUST verify that no regression has been introduced.

### Required checks — run in order

1. **Tests** — `make test` (or equivalent): number of passing tests must be **>=baseline** (count before the change). Zero new failures allowed.
2. **Coverage** — coverage percentage must be **>=baseline**. Never decrease. If no baseline exists, record the current value as baseline.
3. **Lint** — `make lint` (or `ruff check` / `eslint`): warning count must be **= 0**. No increase tolerated.
4. **Types** — `mypy` / `tsc --noEmit`: error count must be **<=baseline**. No new type errors allowed.
5. **Build** — `make build` must exit 0 when applicable.

### Procedure

- Record baseline metrics **before** starting the task (tests passing, coverage %, lint count, type errors).
- After each implementation step, re-run the relevant checks.
- **If any check regresses**: stop, fix the regression, re-run all checks before continuing.
- Do NOT proceed to the next task if any gate is red.

### Reporting

After completing a task, always report:

    Tests : <N> passed (baseline <N>) pass/fail
    Coverage: <X>% (baseline <X>%) pass/fail
    Lint    : 0 warnings pass/fail
    Types   : 0 errors pass/fail
    Build   : ok pass/fail
