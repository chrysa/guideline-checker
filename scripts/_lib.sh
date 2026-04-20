#!/usr/bin/env bash
# _lib.sh — Helpers partagés entre scripts chrysa
# Sourcé via : source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
# ou :       source "$SCRIPTS_DIR/_lib.sh"
#
# Fournit :
#   - Variables de couleur (c_reset, c_blue, c_green, c_yellow, c_red, c_gray, c_cyan)
#   - Fonctions log : info, ok, warn, err, step, header
#   - Fonctions interactives : confirm, ask_choice, ask_string
#   - Path helpers : CHRYSA_ROOT détection, SCRIPTS_DIR
#
# Zero dépendance externe. Compatible bash ≥ 4.0.

# ─── Idempotence (sourceable plusieurs fois) ──────────────
if [[ -n "${_CHRYSA_LIB_LOADED:-}" ]]; then
    return 0
fi
_CHRYSA_LIB_LOADED=1

# ─── Couleurs ────────────────────────────────────────────
c_reset='\033[0m'
c_blue='\033[0;34m'
c_green='\033[0;32m'
c_yellow='\033[0;33m'
c_red='\033[0;31m'
c_gray='\033[0;90m'
c_cyan='\033[0;36m'

# ─── Logging ─────────────────────────────────────────────
info()   { printf "${c_blue}→${c_reset} %s\n" "$*"; }
ok()     { printf "${c_green}✓${c_reset} %s\n" "$*"; }
warn()   { printf "${c_yellow}⚠${c_reset}  %s\n" "$*"; }
err()    { printf "${c_red}✗${c_reset} %s\n" "$*"; }
step()   { printf "\n${c_blue}══ %s ══${c_reset}\n" "$*"; }
header() {
    printf "\n${c_blue}════════════════════════════════════════════${c_reset}\n"
    printf "${c_blue}  %s${c_reset}\n" "$*"
    printf "${c_blue}════════════════════════════════════════════${c_reset}\n\n"
}

# ─── Interactive ─────────────────────────────────────────
# confirm "question" · retourne 0 si y/Y, 1 sinon
# Respecte global AUTO_YES=true pour auto-confirm
confirm() {
    local q=$1
    if [[ "${AUTO_YES:-false}" == "true" ]]; then
        echo "? $q → auto-yes"
        return 0
    fi
    local r
    read -r -p "? $q (y/n) " r </dev/tty
    [[ "$r" =~ ^[Yy]$ ]]
}

# ask_yn "question" [default=y|n]
ask_yn() {
    local q=$1 default=${2:-n} prompt r
    if [[ "${AUTO_YES:-false}" == "true" ]]; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi
    [[ "$default" == "y" ]] && prompt="$q [Y/n] " || prompt="$q [y/N] "
    read -r -p "? $prompt" r </dev/tty
    [[ -z "$r" ]] && r=$default
    [[ "$r" =~ ^[Yy]$ ]]
}

# ask_choice "question" option1 option2 ... · retourne l'option choisie
ask_choice() {
    local q=$1; shift
    local options=("$@")
    echo "? $q"
    local i=1
    for opt in "${options[@]}"; do
        printf "  %d) %s\n" "$i" "$opt"
        i=$((i+1))
    done
    local r
    read -r -p "Choix [1-${#options[@]}] : " r </dev/tty
    [[ "$r" =~ ^[0-9]+$ ]] && (( r >= 1 && r <= ${#options[@]} )) && echo "${options[$((r-1))]}"
}

# ask_string "question" [default] · retourne la saisie ou le default
ask_string() {
    local q=$1 default=${2:-} r
    if [[ -n "$default" ]]; then
        read -r -p "? $q [$default] : " r </dev/tty
        [[ -z "$r" ]] && r=$default
    else
        read -r -p "? $q : " r </dev/tty
    fi
    echo "$r"
}

# ─── Paths ───────────────────────────────────────────────
# Détecte CHRYSA_ROOT · utilise l'env si défini, sinon fallback standard
chrysa_root() {
    if [[ -n "${CHRYSA_ROOT:-}" && -d "$CHRYSA_ROOT" ]]; then
        echo "$CHRYSA_ROOT"
    elif [[ -d "$HOME/Documents/perso/projects/chrysa" ]]; then
        echo "$HOME/Documents/perso/projects/chrysa"
    else
        err "CHRYSA_ROOT introuvable · définir la variable d'environnement"
        return 1
    fi
}

# ─── Garde-fous ──────────────────────────────────────────
# Vérifie que gh CLI est installé + authentifié
check_gh_auth() {
    if ! command -v gh &>/dev/null; then
        err "gh CLI absent · sudo apt install gh"
        return 1
    fi
    if ! gh auth status &>/dev/null; then
        err "gh non authentifié · lance : gh auth login"
        return 1
    fi
    return 0
}

# Vérifie que python3 est présent
check_python() {
    command -v python3 &>/dev/null || { err "python3 absent"; return 1; }
    return 0
}
