# Thin dispatcher over the zero-dep harness. Every target below forwards to
# `cargo harness <target>` (the alias in .cargo/config.toml), which stays the
# single source of truth — this Makefile only routes, so `make ci` ==
# `cargo harness ci`. `bootstrap` is the one-time setup (build + git/agent hooks).

HARNESS := cargo harness

HARNESS_TARGETS := \
	fix lint test audit acceptance coverage mutation arch complexity \
	crap check post-edit stop-hook pre-commit pre-push ci setup-hooks \
	agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps bootstrap $(HARNESS_TARGETS)

deps: ## Fetch + build dependencies
	cargo build

bootstrap: deps setup-hooks ## One-time setup: build + git and agent hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
