#!make
ifneq (,)
	$(error This Makefile requires GNU Make)
endif

# ─── Variables ────────────────────────────────────────────────────────────────
PROJECT_NAME ?= guideline-checker
PYTHON       ?= python3
PIP          ?= pip
PACKAGE_DIR   = guideline_checker

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
	$(PIP) install -e "."

install-dev: ## Install package + dev dependencies
	$(PIP) install -e ".[dev]"
	$(PIP) install ruff mypy pytest pytest-cov

install-pre-commit: ## Install and configure git pre-commit hooks
	$(PIP) install --quiet pre-commit
	pre-commit install
	pre-commit autoupdate --bleeding-edge

# ─── Quality ─────────────────────────────────────────────────────────────────

lint: ## Run ruff linting
	ruff check $(PACKAGE_DIR)

format: ## Run ruff formatter
	ruff format $(PACKAGE_DIR)

format-check: ## Check ruff formatting (no changes)
	ruff format --check $(PACKAGE_DIR)

typecheck: ## Run mypy type checking
	mypy $(PACKAGE_DIR)

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

# ─── Tests ────────────────────────────────────────────────────────────────────

test: ## Run tests
	$(PYTHON) -m pytest tests -v

test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests -v --cov=$(PACKAGE_DIR) --cov-report=xml --cov-report=term-missing --cov-fail-under=85

e2e: ## Run Playwright E2E tests (requires server on port 8080)
	$(PYTHON) -m pytest tests/e2e/ -v --browser chromium

e2e-headed: ## Run E2E tests in headed (visible) browser
	$(PYTHON) -m pytest tests/e2e/ -v --browser chromium --headed

install-e2e: ## Install E2E dependencies (playwright + browsers)
	$(PIP) install -e ".[e2e]"
	playwright install chromium --with-deps
# ─── Docker ───────────────────────────────────────────────────────────────────

docker-build: ## Build all Docker stages
	docker compose build

docker-up: ## Start services with Docker Compose
	docker compose up -d

docker-down: ## Stop services
	docker compose down

docker-test: ## Run tests inside Docker container
	docker compose run --rm test

docker-lint: ## Run lint + type-check inside Docker container
	docker compose run --rm lint

docker-clean: ## Remove Docker images and containers for this project
	docker compose down --rmi local --volumes --remove-orphans

web-local: ## Run the web dashboard natively (no Docker, port 8080)
	$(PYTHON) -m guideline_checker.cli web

web-up: ## Start the web dashboard (containerised, port 8080)
	docker compose up -d web

web-down: ## Stop the web dashboard
	docker compose stop web

web-logs: ## Tail dashboard logs
	docker compose logs -f web

web-build: ## Build the web Docker image
	docker compose build web

# ─── Cleanup ─────────────────────────────────────────────────────────────────

clean: ## Clean build artifacts
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info

# ── Quality Gates ──────────────────────────────────────────────────────────────

quality-gate-baseline: ## Record baseline metrics for regression detection
	@python3 scripts/quality_gate.py baseline

quality-gate-verify: ## Verify no regression since baseline
	@python3 scripts/quality_gate.py verify

# ─── Compat aliases ───────────────────────────────────────────────────────────

type-check: typecheck ## Legacy alias

dev: ## Start development environment (install in editable mode)
	pip install -e .[dev]

build: ## Build package (alias → docker-build)
	$(MAKE) docker-build
