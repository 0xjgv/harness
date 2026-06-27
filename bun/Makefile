# Thin dispatcher over the zero-dep harness. Every target below forwards to
# `bun harness.ts <target>`, which stays the single source of truth — this
# Makefile only routes, so `make ci` == `bun harness.ts ci`. `bootstrap` is the
# one-time setup (dependencies + git/agent hooks).

HARNESS := bun harness.ts

HARNESS_TARGETS := \
	fix lint typecheck test audit acceptance coverage mutation \
	crap complexity deadcode arch check post-edit stop-hook \
	pre-commit pre-push ci setup-hooks agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps bootstrap $(HARNESS_TARGETS)

deps: ## Install / update dependencies
	bun install

bootstrap: deps setup-hooks ## One-time setup: dependencies + git and agent hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
