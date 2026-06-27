# Thin dispatcher over the zero-dep harness. Every target below forwards to
# `go run harness.go <target>`, which stays the single source of truth — this
# Makefile only routes, so `make ci` == `go run harness.go ci`. `bootstrap` is
# the one-time setup (dependencies + git/agent hooks).

HARNESS := go run harness.go

HARNESS_TARGETS := \
	fix lint test test-cov audit complexity acceptance arch mutation \
	crap check post-edit stop-hook pre-commit pre-push ci setup-hooks \
	agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps bootstrap $(HARNESS_TARGETS)

deps: ## Download modules
	go mod download

bootstrap: deps setup-hooks ## One-time setup: dependencies + git and agent hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
