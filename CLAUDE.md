# guideline-checker — CLAUDE.md

## Project purpose

`guideline-checker` is a pre-commit and CLI tool that checks project files against
`.github/instructions/*.instructions.md` rules defined in a repository. It generates
an HTML compliance report and can be configured to fail on warnings or errors.

## Architecture

```
guideline_checker/
  checker.py        — core rule engine (run_checks)
  cli.py            — CLI entry point (guideline-checker check / report)
  hook.py           — pre-commit hook adapter
  loader.py         — .instructions.md file parser (applyTo, rules)
  reporters/
    html.py         — HTML report generation
tests/
  test_checker.py   — checker engine tests
  test_html_reporter.py
  test_loader.py
.pre-commit-hooks.yaml   — hook definition (id: guideline-check)
```

## Key constraints

- Python 3.12+; must work with pre-commit's isolated environment
- No external dependencies beyond the standard library and `jinja2` for the HTML reporter
- The hook runs in `stages: [pre-push, manual]` — not on every commit
- Output to stdout is reserved for pre-commit; use stderr or report files for verbose output
- `--fail-on warning` is the default; can be overridden to `error`

## Development commands

```bash
# Run tests
pytest

# Run pre-commit hooks locally
pre-commit run --all-files

# Test the hook directly
guideline-checker check --fail-on warning

# Build HTML report
guideline-checker report --output report.html
```

## Standards

- Use `ruff check` and `ruff format` for linting/formatting
- Keep functions ≤ 50 lines; files ≤ 500 lines
- All new rule types must have tests in `tests/test_checker.py`
- Coverage target: 80%

## Related

- `chrysa/pre-commit-tools` — ecosystem pre-commit hooks
- `chrysa/shared-standards` — shared Copilot instructions and templates
