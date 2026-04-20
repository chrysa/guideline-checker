# CLAUDE.md — guideline-checker

## Project

**Name:** guideline-checker
**Stack:** Python
**Purpose:** [![CI](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/chrysa/guideline-checker/actions/workflows/ci.yml).

## Working Rules

- Language: English for code, comments, docs, issues and PRs.
- Commits: Conventional Commits (`type(scope): description`).
- Prefer repository make targets when a Makefile is available.
- Read `.github/instructions/*.instructions.md` when present before starting task-specific work.

## Claude Compatibility

- Claude Code hooks are configured in `.claude/settings.json`.
- Shared hooks, thresholds and skills are vendored from `chrysa/shared-standards` into this repository.
- Keep repository-specific overrides in this file and keep generic automation in `.claude/`.

## Read Order

1. `~/.claude/CLAUDE.md` (private user preferences)
2. `CLAUDE.md` (this repository)
3. `.github/copilot-instructions.md`
4. `.github/instructions/*.instructions.md` when present

## Available Skills

Local Claude skills in `.claude/skills/`:
- `testing-pytest` for Python test work
- `dockerfile-multistage` for Dockerfile authoring
- `api-design` for REST and FastAPI/API design tasks

## Repository Notes

- Add repository-specific architecture, operational constraints, or domain rules here when needed.
- If this repository needs extra Claude skills, add them under `.claude/skills/`.
