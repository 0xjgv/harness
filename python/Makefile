# Thin dispatcher over the zero-dep harness. Every target runs inside the exact
# managed Python profile. `workspace` converges a clean clone without changing
# dependency locks; `bootstrap` remains its compatibility alias.

WORKSPACE := .harness/workspace.sh
PROFILE := python
OFFLINE ?= 0
HARNESS = OFFLINE=$(OFFLINE) $(WORKSPACE) exec $(PROFILE) -- uv run --frozen --no-sync harness

HARNESS_TARGETS := \
	fix format lint typecheck test coverage acceptance mutation \
	crap complexity deadcode arch arch-config-guard audit check post-edit stop-hook \
	pre-commit pre-push ci agents-md-drift sync-agents-md clean

.DEFAULT_GOAL := check
.PHONY: deps workspace bootstrap setup-hooks $(HARNESS_TARGETS)

deps: ## Restore dependencies from the committed lock
	@set -eu; \
	if [ "$(OFFLINE)" = 1 ]; then \
		OFFLINE=1 $(WORKSPACE) exec $(PROFILE) -- uv sync --locked --offline; \
	else \
		OFFLINE=0 $(WORKSPACE) exec $(PROFILE) -- uv sync --locked; \
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

setup-hooks:
	OFFLINE=$(OFFLINE) $(WORKSPACE) install-hooks

$(HARNESS_TARGETS):
	$(HARNESS) $@
