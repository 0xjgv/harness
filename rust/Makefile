# Thin dispatcher over the zero-dep harness. Every normal target runs through
# the checked-in workspace provisioner so the exact managed Rust toolchain is
# used without relying on shell-profile state.

OFFLINE ?= 0

WORKSPACE := .harness/workspace.sh
PROFILE := rust
MANAGED_EXEC := OFFLINE=$(OFFLINE) $(WORKSPACE) exec $(PROFILE) --
HARNESS := $(MANAGED_EXEC) cargo run --quiet --locked --bin harness --

HARNESS_TARGETS := \
	fix lint test audit acceptance coverage mutation arch complexity \
	crap check post-edit stop-hook arch-config-guard pre-commit pre-push ci \
	agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps workspace bootstrap setup-hooks $(HARNESS_TARGETS)

deps: ## Restore and build the exact locked dependency graph
	$(MANAGED_EXEC) cargo fetch --locked
	$(MANAGED_EXEC) cargo build --locked

workspace: ## Provision an autonomous, deterministic agent workspace
	OFFLINE=$(OFFLINE) $(WORKSPACE) preflight $(PROFILE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) install $(PROFILE)
	$(MAKE) --no-print-directory deps OFFLINE=$(OFFLINE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) sync-skills
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks
	OFFLINE=$(OFFLINE) $(WORKSPACE) verify $(PROFILE)
	$(MAKE) --no-print-directory check OFFLINE=$(OFFLINE)
	OFFLINE=$(OFFLINE) $(WORKSPACE) verify $(PROFILE)

bootstrap: workspace ## Compatibility alias for workspace

setup-hooks: ## Install collision-safe managed Git and agent hooks
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
