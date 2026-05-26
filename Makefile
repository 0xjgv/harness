# harness-templates root Makefile
#
# Owns drift + sync between this repo's canonical `skills/harness/` and the
# two deployed locations Claude Code (`~/.claude/skills/harness/`) and Codex
# (`~/.agents/skills/harness/`) actually read. Template content lives under
# `python/`, `bun/`, `go/`, `rust/`, `monorepo/` — each has its own harness;
# this Makefile does not dispatch into them.

SHELL := /bin/bash
.DEFAULT_GOAL := help

GREEN := \033[32m
RED   := \033[31m
DIM   := \033[2m
BOLD  := \033[1m
RESET := \033[0m

CANONICAL := skills/harness
TARGETS   := $(HOME)/.claude/skills/harness $(HOME)/.agents/skills/harness
FILES     := SKILL.md \
             reference-behavior-contract.md \
             reference-settings-json.md \
             reference-python.md \
             reference-bun.md \
             reference-go.md \
             reference-rust.md \
             reference-monorepo.md

.PHONY: check
check: skills-drift ## Run all repo-level gates (currently just skills-drift)

.PHONY: skills-drift
skills-drift: ## Fail if deployed skill copies diverge from skills/harness/
	@set -u; failed=0; \
	for tgt in $(TARGETS); do \
	  for f in $(FILES); do \
	    src="$(CANONICAL)/$$f"; dst="$$tgt/$$f"; \
	    if [ ! -f "$$src" ]; then \
	      printf "  $(RED)✗$(RESET) skills-drift: canonical $$src missing\n"; failed=1; continue; \
	    fi; \
	    if [ ! -f "$$dst" ]; then \
	      printf "  $(RED)✗$(RESET) skills-drift: $$dst missing — run \`make sync-skills\`\n"; failed=1; continue; \
	    fi; \
	    if ! cmp -s "$$src" "$$dst"; then \
	      printf "  $(RED)✗$(RESET) skills-drift: $$dst differs from $$src — run \`make sync-skills\`\n"; \
	      diff -u "$$src" "$$dst" | head -20; \
	      failed=1; \
	    fi; \
	  done; \
	done; \
	if [ $$failed -eq 0 ]; then \
	  printf "  $(GREEN)✓$(RESET) skills-drift (canonical == $(words $(TARGETS)) targets)\n"; \
	else \
	  exit 1; \
	fi

.PHONY: sync-skills
sync-skills: ## Copy skills/harness/ → ~/.claude and ~/.agents
	@set -u; \
	for tgt in $(TARGETS); do \
	  mkdir -p "$$tgt"; \
	  for f in $(FILES); do \
	    cp "$(CANONICAL)/$$f" "$$tgt/$$f"; \
	  done; \
	  printf "  $(GREEN)✓$(RESET) sync-skills: $$tgt ← $(CANONICAL)\n"; \
	done

.PHONY: help
help: ## Show this message
	@printf "$(BOLD)harness-templates$(RESET) — repo-level gates.\n\n"
	@awk 'BEGIN { FS = ":.*## " } \
	     /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
