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

type-check: ## Run mypy type checking
	mypy $(PACKAGE_DIR)

pre-commit: ## Run pre-commit on all files
	pre-commit run --all-files

# ─── Tests ────────────────────────────────────────────────────────────────────

test: ## Run tests
	pytest tests -v

test-cov: ## Run tests with coverage
	pytest tests -v --cov=$(PACKAGE_DIR) --cov-report=term-missing

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
