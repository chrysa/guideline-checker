#!/usr/bin/env bash
# quality-check.sh — hook Claude Code · lance make quality avant commit
# Déclenché via .claude/settings.json PreToolUse/Bash ou PostToolUse/Write
# Retourne exit != 0 pour bloquer l'action si seuils dépassés.

set -uo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

if [[ -f "Makefile" ]] && grep -qE "^quality:" Makefile; then
    make quality
elif [[ -f "pyproject.toml" ]] && command -v ruff &>/dev/null; then
    ruff check . && ruff format --check .
    command -v mypy &>/dev/null && mypy . || true
elif [[ -f "package.json" ]]; then
    npm run lint --silent 2>/dev/null || pnpm lint 2>/dev/null
    npm run type-check --silent 2>/dev/null || pnpm exec tsc --noEmit 2>/dev/null
fi
