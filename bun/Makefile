# Thin dispatcher over the zero-dep harness. Every target runs inside the exact
# managed Bun profile. `workspace` converges a clean clone without changing
# dependency locks; `bootstrap` remains its compatibility alias.

WORKSPACE := .harness/workspace.sh
PROFILE := bun
OFFLINE ?= 0
HARNESS = OFFLINE=$(OFFLINE) $(WORKSPACE) exec $(PROFILE) -- bun harness.ts

HARNESS_TARGETS := \
	fix lint typecheck test audit acceptance coverage mutation \
	crap complexity deadcode arch arch-config-guard check post-edit stop-hook \
	pre-commit pre-push ci agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps workspace bootstrap setup-hooks $(HARNESS_TARGETS)

deps: ## Restore dependencies from the committed lock
	@set -eu; \
	if [ "$(OFFLINE)" = 1 ]; then \
		OFFLINE=1 $(WORKSPACE) exec $(PROFILE) -- bun install --frozen-lockfile --offline; \
	else \
		OFFLINE=0 $(WORKSPACE) exec $(PROFILE) -- bun install --frozen-lockfile; \
	fi

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

setup-hooks: ## Install managed Git and agent Stop hooks
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
