#!make
# makefile-tier: python-app

# ─── Variables ────────────────────────────────────────────────────────────────
PROJECT_NAME ?= guideline-checker
PYTHON       ?= python3
PIP          ?= pip
PACKAGE_DIR   = guideline_checker

# ─── Artifact isolation — tool caches out of the repo tree ────────────────────
UID ?= $(shell id -u)
GID ?= $(shell id -g)
_CACHE_BASE ?= $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)/chrysa/$(PROJECT_NAME)
RUFF_CACHE_DIR ?= $(_CACHE_BASE)/ruff
MYPY_CACHE_DIR ?= $(_CACHE_BASE)/mypy
PYTHONPYCACHEPREFIX ?= $(_CACHE_BASE)/pycache
PYTEST_ADDOPTS ?= -p no:cacheprovider
export UID GID RUFF_CACHE_DIR MYPY_CACHE_DIR PYTHONPYCACHEPREFIX PYTEST_ADDOPTS

.DEFAULT_GOAL := help

.PHONY: $(shell grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | cut -d":" -f1 | tr "\n" " ")

help: ## Display this help message
	@echo "==================================================================="
	@echo "  $(PROJECT_NAME)"
	@echo "==================================================================="
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "==================================================================="

# ─── Installation ────────────────────────────────────────────────────────────

install: ## Install package dependencies
	@$(PIP) install -e "."

install-dev: ## Install package + dev dependencies
	@$(PIP) install -e ".[dev]"
	@$(PIP) install ruff mypy pytest pytest-cov

install-pre-commit: ## Install and configure git pre-commit hooks
	@$(PIP) install --quiet pre-commit
	@pre-commit install
	@pre-commit autoupdate --bleeding-edge

# ─── Quality ─────────────────────────────────────────────────────────────────

lint: ## Run ruff linting
	@ruff check $(PACKAGE_DIR)

format: ## Run ruff formatter
	@ruff format $(PACKAGE_DIR)

format-check: ## Check ruff formatting (no changes)
	@ruff format --check $(PACKAGE_DIR)

typecheck: ## Run mypy type checking
	@mypy $(PACKAGE_DIR)

pre-commit: ## Run pre-commit on all files
	@pre-commit run --all-files

# ─── Tests ────────────────────────────────────────────────────────────────────

test: ## Run tests
	@$(PYTHON) -m pytest tests -v

test-cov: ## Run tests with coverage report
	@$(PYTHON) -m pytest tests -v --cov=$(PACKAGE_DIR) --cov-report=xml --cov-report=term-missing --cov-fail-under=85

# The E2E suite starts its own server, so no port needs to be up first.
# PYTEST_DISABLE_PLUGIN_AUTOLOAD keeps the run hermetic: E2E must execute on the
# host to drive a browser, and any pytest plugin installed there — including a
# broken one — would otherwise load and kill collection. The plugins the suite
# actually needs are named explicitly.
E2E_PYTEST = PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest tests/e2e/ -v \
	--browser chromium -p pytest_playwright.pytest_playwright -p pytest_cov --no-cov

e2e: ## Run Playwright E2E tests (starts its own server)
	@$(E2E_PYTEST)

e2e-headed: ## Run E2E tests in headed (visible) browser
	@$(E2E_PYTEST) --headed

install-e2e: ## Install E2E dependencies (playwright + browsers)
	@$(PIP) install -e ".[e2e]"
	@playwright install chromium --with-deps
# ─── Docker ───────────────────────────────────────────────────────────────────

docker-build: ## Build all Docker stages
	@docker compose build

docker-up: ## Start services with Docker Compose
	@docker compose up -d

docker-down: ## Stop services
	@docker compose down

docker-test: ## Run tests inside Docker container
	@# The test service bind-mounts ./coverage.xml into the container so pytest-cov can
	@# write the report to the host. Docker auto-creates a *directory* for a missing
	@# bind source, which then makes coverage's `open(path, "w")` fail (IsADirectoryError)
	@# and crashes the whole run. Pre-create it as a file so the mount maps file -> file.
	@rm -rf coverage.xml
	@touch coverage.xml
	@# -T disables pseudo-TTY allocation so the target also runs from non-interactive
	@# contexts (CI, the run-tests pre-push hook), which otherwise fail with
	@# "the input device is not a TTY".
	@# --build is not optional: the test image COPYs the sources, so without it a run
	@# reuses a stale image and reports a pass for code it never executed. The pre-push
	@# hook runs this target, so the omission let the gate go green on unbuilt code.
	@# Costs ~11s when nothing changed (layer cache), which is the price of the gate
	@# meaning what it says.
	@docker compose run --rm -T --build test

docker-lint: ## Run lint + type-check inside Docker container
	@docker compose run --rm -T lint

docker-clean: ## Remove Docker images and containers for this project
	@docker compose down --rmi local --volumes --remove-orphans

web-local: ## Run the web dashboard natively (no Docker, port 8080)
	@$(PYTHON) -m guideline_checker.cli web

web-up: ## Start the web dashboard (containerised, port 8080)
	@docker compose up -d frontend

web-down: ## Stop the web dashboard
	@docker compose stop frontend

web-logs: ## Tail dashboard logs
	@docker compose logs -f frontend

web-build: ## Build the web Docker image
	@docker compose build frontend

# ─── Cleanup ─────────────────────────────────────────────────────────────────

clean: ## Clean build artifacts
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info

# ── Quality Gates ──────────────────────────────────────────────────────────────

quality-gate-baseline: ## Record baseline metrics for regression detection
	@python3 scripts/quality_gate.py baseline

quality-gate-verify: ## Verify no regression since baseline
	@python3 scripts/quality_gate.py verify

# ─── Release ─────────────────────────────────────────────────────────────────

changelog: ## Regenerate CHANGELOG.md from the git history (git-cliff)
	@git-cliff --output CHANGELOG.md

# ─── Compat aliases ───────────────────────────────────────────────────────────

dev: ## Install package + dev dependencies (alias → install-dev)
	@$(MAKE) install-dev

build: ## Build the Docker images (alias → docker-build)
	@$(MAKE) docker-build

# ─── CI gate ─────────────────────────────────────
ci: lint format-check typecheck docker-test ## Run the full local gate (lint + format + typecheck + tests)
