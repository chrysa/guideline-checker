#!/usr/bin/env bash
# check-standards-sync.sh — hook pre-commit (mode warn-only)
# Détecte drift entre les fichiers standards locaux d'un repo
# et les templates source-de-vérité dans shared-standards/templates/.
#
# Philosophie :
#   - WARN-ONLY par défaut : ne bloque PAS le commit
#   - --strict bloque le commit si drift détecté (exit 1)
#   - --auto-update applique les mises à jour depuis templates (dangereux, confirmer)
#
# Usage :
#   bash check-standards-sync.sh              # warn-only (default pre-commit)
#   bash check-standards-sync.sh --strict     # bloque si drift
#   bash check-standards-sync.sh --auto-update  # écrase local avec template
#
# Intégration pre-commit :
#   - repo: local
#     hooks:
#       - id: check-standards-sync
#         name: Vérifie que les standards chrysa sont à jour
#         entry: bash shared-standards/scripts/check-standards-sync.sh
#         language: system
#         pass_filenames: false
#         always_run: true

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHRYSA_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATES="$CHRYSA_ROOT/shared-standards/templates"
source "$SCRIPT_DIR/_lib.sh" 2>/dev/null || true

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

STRICT=false
AUTO_UPDATE=false
for arg in "$@"; do
    case "$arg" in
        --strict) STRICT=true ;;
        --auto-update) AUTO_UPDATE=true ;;
        -h|--help)
            sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

# ─── Pairs (template_source, target_local) ──────────────
# Les fichiers à surveiller. Si drift détecté → warn ou update.
declare -a CHECKS=(
    "$TEMPLATES/GitVersion.yml:GitVersion.yml"
    "$TEMPLATES/labels.yml:.github/labels.yml"
    "$TEMPLATES/labeler.yml:.github/labeler.yml"
    "$TEMPLATES/dependabot.yml:.github/dependabot.yml"
    "$TEMPLATES/pr-template.md:.github/pull_request_template.md"
    "$TEMPLATES/CODEOWNERS:.github/CODEOWNERS"
    "$TEMPLATES/github-workflows/dependabot-auto-merge.yml:.github/workflows/dependabot-auto-merge.yml"
    "$TEMPLATES/github-workflows/branch-auto-update.yml:.github/workflows/branch-auto-update.yml"
    "$TEMPLATES/github-workflows/enforce-issue-link.yml:.github/workflows/enforce-issue-link.yml"
    "$TEMPLATES/github-workflows/labeler.yml:.github/workflows/labeler.yml"
    "$TEMPLATES/github-workflows/sync-labels.yml:.github/workflows/sync-labels.yml"
    "$TEMPLATES/github-workflows/pr-size-label.yml:.github/workflows/pr-size-label.yml"
    "$TEMPLATES/github-workflows/_reusable-ci.yml:.github/workflows/_reusable-ci.yml"
    "$TEMPLATES/claude/config.json:.claude/config.json"
    "$TEMPLATES/claude/settings.json:.claude/settings.json"
    "$TEMPLATES/claude/rules/thresholds.md:.claude/rules/thresholds.md"
)

# ─── Compare ─────────────────────────────────────────────
drift_count=0
missing_count=0
total=${#CHECKS[@]}

cd "$REPO_ROOT" || exit 1

for check in "${CHECKS[@]}"; do
    src="${check%%:*}"
    dst="${check##*:}"

    if [[ ! -f "$src" ]]; then
        continue  # Template source absent, skip
    fi

    if [[ ! -f "$dst" ]]; then
        missing_count=$((missing_count + 1))
        if [[ -n "${c_yellow:-}" ]]; then
            printf "${c_yellow}[missing]${c_reset} %s · absent localement\n" "$dst"
        else
            echo "[missing] $dst · absent localement"
        fi
        if $AUTO_UPDATE; then
            mkdir -p "$(dirname "$dst")"
            cp "$src" "$dst"
            echo "    → copié depuis $src"
        fi
        continue
    fi

    if ! diff -q "$src" "$dst" >/dev/null 2>&1; then
        drift_count=$((drift_count + 1))
        if [[ -n "${c_yellow:-}" ]]; then
            printf "${c_yellow}[drift]${c_reset} %s · diverge de %s\n" "$dst" "$src"
        else
            echo "[drift] $dst · diverge de $src"
        fi
        if $AUTO_UPDATE; then
            cp "$src" "$dst"
            echo "    → écrasé avec template"
        fi
    fi
done

# ─── Rapport ─────────────────────────────────────────────
total_issues=$((drift_count + missing_count))

echo ""
if [[ "$total_issues" -eq 0 ]]; then
    if [[ -n "${c_green:-}" ]]; then
        printf "${c_green}✓${c_reset} Standards à jour (%d/%d fichiers alignés)\n" "$total" "$total"
    else
        echo "✓ Standards à jour"
    fi
    exit 0
fi

echo "Résultat : $drift_count drift · $missing_count missing · $total vérifiés"
echo ""
echo "Pour fixer :"
echo "  bash shared-standards/scripts/apply-standards.sh --repo=\$(basename \$(pwd)) --force"
echo "  ou : bash shared-standards/scripts/check-standards-sync.sh --auto-update"

if $STRICT; then
    if [[ -n "${c_red:-}" ]]; then
        printf "${c_red}✗${c_reset} Mode strict : commit bloqué tant que drift non résolu\n"
    else
        echo "✗ Mode strict : commit bloqué"
    fi
    exit 1
fi

if [[ -n "${c_yellow:-}" ]]; then
    printf "${c_yellow}⚠${c_reset}  Drift détecté (warn-only, commit autorisé)\n"
else
    echo "⚠ Drift détecté (warn-only)"
fi
exit 0
