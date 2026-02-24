# Silent helper (set VERBOSE=1 for full output)
SILENT_HELPER := source scripts/run_silent.sh

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Setup

.PHONY: install
install: ## Install dependencies with uv
	@$(SILENT_HELPER) && \
		print_main_header "Installing Dependencies" && \
		run_silent "Sync dependencies" "uv sync"

.PHONY: install-global
install-global: ## Install as global CLI tool
	@$(SILENT_HELPER) && \
		run_silent "Install global tool" "uv tool install --editable . --force"

.PHONY: uninstall-global
uninstall-global: ## Uninstall global CLI tool
	@$(SILENT_HELPER) && \
		run_silent "Uninstall global tool" "uv tool uninstall entropy-meter"

##@ Code Quality

.PHONY: fix
fix: ## Fix lint errors with ruff
	@$(SILENT_HELPER) && run_silent "Fix lint errors" "uv run ruff check --fix ."

.PHONY: format
format: ## Format code with ruff
	@$(SILENT_HELPER) && run_silent "Format code" "uv run ruff format ."

.PHONY: lint
lint: ## Lint code with ruff
	@$(SILENT_HELPER) && run_silent "Lint check" "uv run ruff check ."

.PHONY: typecheck
typecheck: ## Type-check with basedpyright
	@$(SILENT_HELPER) && run_silent "Type check" "uv run basedpyright entropy_meter/"

.PHONY: check
check: ## Run all quality checks (fix, format, lint, typecheck)
	@$(SILENT_HELPER) && print_main_header "Running Quality Checks"
	@$(MAKE) fix
	@$(MAKE) format
	@$(MAKE) lint
	@$(MAKE) typecheck

.PHONY: check-fast
check-fast: ## Run quality checks on staged Python files only
	@files=$$(git diff --name-only --cached | grep -E '^(entropy_meter|tests)/.*\.py$$' | tr '\n' ' '); \
	if [ -z "$$files" ]; then \
		echo "No staged Python files — skipping checks"; \
	else \
		$(SILENT_HELPER) && \
		run_silent "Fix lint errors" "uv run ruff check --fix $$files" && \
		run_silent "Format code" "uv run ruff format $$files" && \
		run_silent "Lint check" "uv run ruff check $$files" && \
		run_silent "Type check" "uv run basedpyright entropy_meter/"; \
	fi

##@ Testing

.PHONY: test
test: ## Run tests with pytest
	@$(SILENT_HELPER) && run_silent_with_test_count "Run tests" "uv run pytest -x -q"

.PHONY: test-cov
test-cov: ## Run tests with coverage (80% minimum)
	@$(SILENT_HELPER) && run_silent_with_test_count "Run tests with coverage" "uv run pytest --cov --cov-report=term-missing"

##@ Workflow

.PHONY: pre-commit
pre-commit: ## Run pre-commit checks (quality gates + tests if source files staged)
	@$(MAKE) check-fast
	@if git diff --cached --name-only | grep -qE '^(entropy_meter|tests)/.*\.py$$'; then \
		$(MAKE) test; \
	fi

.PHONY: hooks
hooks: ## Install git pre-commit hook
	@printf '#!/bin/sh\nmake pre-commit\n' > .git/hooks/pre-commit && \
		chmod +x .git/hooks/pre-commit && \
		echo "✓ Installed pre-commit hook"

##@ Maintenance

.PHONY: clean
clean: ## Remove cache and build artifacts
	@$(SILENT_HELPER) && \
		print_main_header "Cleaning Up" && \
		run_silent "Remove caches" "rm -rf .pytest_cache .ruff_cache __pycache__ */__pycache__ */*/__pycache__ build dist *.egg-info htmlcov .coverage" && \
		run_silent "Ruff clean" "uv run ruff clean"
