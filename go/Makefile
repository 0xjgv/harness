# Thin dispatcher over the zero-dep harness. Every target runs inside the exact
# managed Go profile. `workspace` converges a clean clone without changing
# dependency locks; `bootstrap` remains its compatibility alias.

WORKSPACE := .harness/workspace.sh
PROFILE := go
OFFLINE ?= 0
HARNESS = OFFLINE=$(OFFLINE) $(WORKSPACE) exec $(PROFILE) -- go run -mod=readonly harness.go

HARNESS_TARGETS := \
	fix lint test test-cov audit complexity acceptance arch mutation \
	crap check post-edit stop-hook arch-config-guard pre-commit pre-push ci \
	agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps workspace bootstrap setup-hooks $(HARNESS_TARGETS)

deps: ## Restore modules without changing go.mod or go.sum
	OFFLINE=$(OFFLINE) $(WORKSPACE) exec $(PROFILE) -- env GOFLAGS=-mod=readonly go mod download

workspace: ## Converge an autonomous, deterministic workspace
	OFFLINE=$(OFFLINE) $(WORKSPACE) preflight $(PROFILE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) install $(PROFILE)
	$(MAKE) deps OFFLINE=$(OFFLINE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) sync-skills
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks
	OFFLINE=$(OFFLINE) $(WORKSPACE) verify $(PROFILE)
	$(MAKE) check OFFLINE=$(OFFLINE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) verify $(PROFILE)

bootstrap: workspace ## Compatibility alias for workspace

setup-hooks: ## Install collision-safe managed Git hooks
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
