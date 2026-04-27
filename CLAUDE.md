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
  __init__.py           # Package version
  checker.py            # Core check engine — runs rules against source files
  cli.py                # CLI entry point (argparse) — check subcommand
  hook.py               # Pre-commit hook entry point (delegates to cli.main)
  loader.py             # Instruction file loader and parser
  reporters/
    html.py             # HTML report generator (string templates)
    json_reporter.py    # JSON report output
.pre-commit-hooks.yaml  # Hook definition for pre-commit framework
tests/
  test_checker.py       # Core engine tests
  test_cli.py           # CLI entry point tests
  test_hook.py          # Hook entry point tests
  test_html_reporter.py # HTML reporter tests
  test_json_reporter.py # JSON reporter tests
  test_loader.py        # Loader tests
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
