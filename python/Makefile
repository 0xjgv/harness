# Thin dispatcher over the zero-dep harness. Every target below forwards to
# `uv run harness <target>`, which stays the single source of truth — this
# Makefile only routes, so `make ci` == `uv run harness ci`. `bootstrap` is the
# one-time setup (dependencies + git/agent hooks).

HARNESS := uv run harness

HARNESS_TARGETS := \
	fix format lint typecheck test coverage acceptance mutation \
	crap complexity deadcode arch audit check post-edit stop-hook \
	pre-commit pre-push ci setup-hooks agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps bootstrap $(HARNESS_TARGETS)

deps: ## Install / update dependencies
	uv sync

bootstrap: deps setup-hooks ## One-time setup: dependencies + git and agent hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
