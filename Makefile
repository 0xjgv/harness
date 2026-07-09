# harness-templates root Makefile
#
# Owns repo-level dogfooding for harness-templates:
# - drift + sync between the canonical `skills/harness/` source and the two
#   deployed locations Claude Code/Codex actually read
# - AGENTS.md/CLAUDE.md parity at the meta-repo root
# - root Claude/Codex Stop hook entrypoints
# - thin dispatch into dirty language templates for post-edit/stop-hook

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
             reference/behavior-contract.md \
             reference/adoption-checklist.md \
             reference/settings-json.md \
             reference/python.md \
             reference/bun.md \
             reference/go.md \
             reference/rust.md \
             reference/monorepo.md

BUN_DIRS  := $(patsubst %/harness.ts,%,$(wildcard */harness.ts))
PY_DIRS   := $(patsubst %/harness.py,%,$(wildcard */harness.py))
GO_DIRS   := $(patsubst %/harness.go,%,$(wildcard */harness.go))
RUST_DIRS := $(patsubst %/Cargo.toml,%,$(wildcard */Cargo.toml))
SUBPROJECTS := $(sort $(BUN_DIRS) $(PY_DIRS) $(GO_DIRS) $(RUST_DIRS))

define SH_LANG_HELPERS
lang_of() {
  if   [ -f "$$1/harness.ts" ];  then echo bun;
  elif [ -f "$$1/harness.py" ];  then echo python;
  elif [ -f "$$1/harness.go" ];  then echo go;
  elif [ -f "$$1/Cargo.toml" ];  then echo rust;
  else echo unknown; fi
}
runner_of() {
  if   [ -f "$$1/harness.ts" ];  then echo "bun harness.ts";
  elif [ -f "$$1/harness.py" ];  then echo "uv run harness";
  elif [ -f "$$1/harness.go" ];  then echo "go run harness.go";
  elif [ -f "$$1/Cargo.toml" ];  then echo "cargo harness";
  else return 1; fi
}
endef
export SH_LANG_HELPERS

define SH_FILTER_DIRS
filter_dirs() {
  local files p dirs=""
  files=$$(eval "$$1" 2>/dev/null) || return 0
  [ -z "$$files" ] && return 0
  for p in $(SUBPROJECTS); do
    echo "$$files" | grep -q "^$$p/" && dirs="$$dirs $$p"
  done
  printf '%s' "$$dirs"
}
dirty_dirs()  { filter_dirs 'git diff --name-only --diff-filter=d; git ls-files --others --exclude-standard'; }
staged_dirs() { filter_dirs 'git diff --cached --name-only --diff-filter=d'; }
endef
export SH_FILTER_DIRS

define SH_ARCH_CONFIG_GUARD
arch_config_filter() {
  grep -E '(^|/)(\.importlinter|\.dependency-cruiser\.json|\.go-arch-lint\.yml|arch\.toml)$$' | sort -u
}
arch_config_changed_paths() {
  local staged="$$1" include_pre_push="$$2" base=""
  if [ "$$staged" = 1 ]; then
    git diff --cached --name-only --diff-filter=d 2>/dev/null || true
    return 0
  fi
  git diff --name-only --diff-filter=d 2>/dev/null || true
  git diff --cached --name-only --diff-filter=d 2>/dev/null || true
  git ls-files --others --exclude-standard 2>/dev/null || true
  if [ -n "$${HARNESS_ARCH_BASE:-}" ]; then
    base="$$HARNESS_ARCH_BASE"
  elif [ -n "$${GITHUB_BASE_REF:-}" ]; then
    base="origin/$$GITHUB_BASE_REF"
  fi
  if [ -n "$$base" ] && git rev-parse --verify "$$base" >/dev/null 2>&1; then
    git diff --name-only --diff-filter=d "$$base...HEAD" 2>/dev/null || true
  fi
  if [ "$$include_pre_push" = 1 ] && [ ! -t 0 ]; then
    local local_ref local_sha remote_ref remote_sha zero
    zero=0000000000000000000000000000000000000000
    while read -r local_ref local_sha remote_ref remote_sha; do
      [ -z "$$local_sha" ] && continue
      [ "$$local_sha" = "$$zero" ] && continue
      if [ "$$remote_sha" = "$$zero" ]; then
        git diff-tree --no-commit-id --name-only -r "$$local_sha" 2>/dev/null || true
      else
        git diff --name-only --diff-filter=d "$$remote_sha" "$$local_sha" 2>/dev/null || true
      fi
    done
  fi
}
arch_config_guard() {
  local staged="$$1" warn_only="$$2" include_pre_push="$$3" changed
  changed=$$(arch_config_changed_paths "$$staged" "$$include_pre_push" | arch_config_filter)
  if [ -z "$$changed" ]; then
    printf "  $(GREEN)✓$(RESET) Arch config guard\n"
    return 0
  fi
  changed=$$(printf '%s\n' "$$changed" | awk 'BEGIN { sep="" } { printf "%s%s", sep, $$0; sep=", " } END { print "" }')
  if [ "$${HARNESS_ALLOW_ARCH_CONFIG:-}" = 1 ]; then
    printf "  $(GREEN)⚠$(RESET) Arch config guard override: %s\n" "$$changed"
    return 0
  fi
  if [ "$$warn_only" = 1 ]; then
    printf "  $(GREEN)⚠$(RESET) Arch config changed: %s\n" "$$changed"
    printf "  ↳ fix: review intentionally, then use HARNESS_ALLOW_ARCH_CONFIG=1 for commit/push/CI\n"
    return 0
  fi
  printf "  $(RED)✗$(RESET) Arch config changed: %s\n" "$$changed"
  printf "  ↳ fix: review intentionally, then rerun with HARNESS_ALLOW_ARCH_CONFIG=1\n"
  return 1
}
endef
export SH_ARCH_CONFIG_GUARD

.PHONY: check
check: skills-drift agents-md-drift ## Run repo-level gates
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 1 0

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
	  rm -f "$$tgt"/reference-*.md "$$tgt"/reference/reference-*.md; \
	  for f in $(FILES); do \
	    mkdir -p "$$tgt/$$(dirname "$$f")"; \
	    cp "$(CANONICAL)/$$f" "$$tgt/$$f"; \
	  done; \
	  printf "  $(GREEN)✓$(RESET) sync-skills: $$tgt ← $(CANONICAL)\n"; \
	done

.PHONY: agents-md-drift
agents-md-drift: ## Fail if root AGENTS.md differs from CLAUDE.md
	@set -u; \
	if [ ! -f CLAUDE.md ]; then \
	  printf "  $(RED)✗$(RESET) agents-md-drift: CLAUDE.md not found\n"; exit 1; \
	fi; \
	if [ ! -f AGENTS.md ]; then \
	  printf "  $(RED)✗$(RESET) agents-md-drift: AGENTS.md missing — run \`make sync-agents-md\`\n"; exit 1; \
	fi; \
	if cmp -s CLAUDE.md AGENTS.md; then \
	  printf "  $(GREEN)✓$(RESET) agents-md-drift\n"; \
	else \
	  line=$$(diff CLAUDE.md AGENTS.md | grep -m1 -E '^[0-9]' || echo "?"); \
	  printf "  $(RED)✗$(RESET) agents-md-drift: AGENTS.md differs from CLAUDE.md (diff: %s) — run \`make sync-agents-md\`\n" "$$line"; \
	  exit 1; \
	fi

.PHONY: sync-agents-md
sync-agents-md: ## Overwrite root AGENTS.md from CLAUDE.md
	@set -u; \
	if [ ! -f CLAUDE.md ]; then \
	  printf "  $(RED)✗$(RESET) sync-agents-md: CLAUDE.md not found\n"; exit 1; \
	fi; \
	cp CLAUDE.md AGENTS.md; \
	printf "  $(GREEN)✓$(RESET) sync-agents-md: AGENTS.md ← CLAUDE.md\n"

.PHONY: arch-config-guard
arch-config-guard: ## Block unreviewed arch config changes; pass ARGS=--warn for advisory mode
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; \
	warn=0; staged=0; \
	case " $(ARGS) " in *" --warn "*) warn=1 ;; esac; \
	case " $(ARGS) " in *" --staged "*) staged=1 ;; esac; \
	arch_config_guard "$$staged" "$$warn" 0

.PHONY: post-edit
post-edit: ## Root Stop helper: sync derived docs/skills and format dirty templates
	@set -u -o pipefail; \
	claude_dirty=$$(git diff --name-only --diff-filter=d -- CLAUDE.md; git ls-files --others --exclude-standard -- CLAUDE.md); \
	if [ -n "$$claude_dirty" ]; then $(MAKE) --no-print-directory sync-agents-md; fi; \
	skill_dirty=$$(git diff --name-only --diff-filter=d -- skills/harness; git ls-files --others --exclude-standard -- skills/harness); \
	if [ -n "$$skill_dirty" ]; then $(MAKE) --no-print-directory sync-skills; fi; \
	eval "$$SH_FILTER_DIRS"; dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=post-edit DIRS="$$dirs" QUIET=1

.PHONY: stop-hook
stop-hook: ## Agent Stop hook: post-edit, arch-config warning, dirty template stop-hooks
	@printf "\n=== Root Stop Hook Checks ===\n"
	@$(MAKE) --no-print-directory post-edit
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 1 0
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=stop-hook DIRS="$$dirs"

.PHONY: pre-commit
pre-commit: ## Root git pre-commit hook
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 1 0 0
	@$(MAKE) --no-print-directory agents-md-drift
	@$(MAKE) --no-print-directory skills-drift
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; dirs=$$(staged_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=pre-commit DIRS="$$dirs"

.PHONY: pre-push
pre-push: ## Root git pre-push hook
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 0 1
	@$(MAKE) --no-print-directory agents-md-drift
	@$(MAKE) --no-print-directory skills-drift
	@$(MAKE) --no-print-directory _run CMD=pre-push DIRS="$(SUBPROJECTS)"

.PHONY: ci
ci: ## Root read-only verification
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 0 0
	@$(MAKE) --no-print-directory agents-md-drift
	@$(MAKE) --no-print-directory skills-drift
	@$(MAKE) --no-print-directory _run CMD=ci DIRS="$(SUBPROJECTS)"

.PHONY: audit
audit: ## Dependency audit across language templates
	@$(MAKE) --no-print-directory _run CMD=audit DIRS="$(SUBPROJECTS)"

.PHONY: check-dirty
check-dirty: ## Run check only in templates with working-tree changes
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && { printf "$(DIM)Nothing to check.$(RESET)\n"; exit 0; }; \
	$(MAKE) --no-print-directory _run CMD=check DIRS="$$dirs"

.PHONY: setup-hooks
setup-hooks: ## Install root pre-commit/pre-push hooks and verify Stop hook wiring
	@if ! env -u GIT_DIR -u GIT_WORK_TREE git rev-parse --git-dir >/dev/null 2>&1; then \
	  printf "$(RED)Not a git repo.$(RESET) Run 'git init' first.\n"; exit 1; \
	fi
	@set -eu; for name in pre-commit pre-push; do \
	  hook=$$(env -u GIT_DIR -u GIT_WORK_TREE git rev-parse --git-path "hooks/$$name"); \
	  mkdir -p "$$(dirname "$$hook")"; \
	  printf '#!/bin/sh\nexec make %s\n' "$$name" > "$$hook"; \
	  chmod +x "$$hook"; \
	  printf "  $(GREEN)✓$(RESET) Installed %s → make %s\n" "$$hook" "$$name"; \
	done
	@if [ -f .claude/settings.json ] && grep -q 'Stop' .claude/settings.json && grep -q 'stop-hook' .claude/settings.json; then \
	  printf "  $(GREEN)✓$(RESET) Stop hook wiring (.claude/settings.json)\n"; \
	else \
	  printf "  $(RED)⚠$(RESET) Missing Stop hook wiring: .claude/settings.json\n"; \
	fi
	@if [ -f .codex/hooks.json ] && grep -q 'Stop' .codex/hooks.json && grep -q 'stop-hook' .codex/hooks.json; then \
	  printf "  $(GREEN)✓$(RESET) Stop hook wiring (.codex/hooks.json)\n"; \
	else \
	  printf "  $(RED)⚠$(RESET) Missing Stop hook wiring: .codex/hooks.json\n"; \
	fi

.PHONY: list
list: ## Show detected language templates
	@set -u; eval "$$SH_LANG_HELPERS"; \
	for d in $(SUBPROJECTS); do printf "  \033[36m%-12s\033[0m %s\n" "$$d" "$$(lang_of "$$d")"; done

.PHONY: _run
_run:
	@set -u -o pipefail; \
	dirs="$(DIRS)"; cmd="$(CMD)"; args="$(ARGS)"; quiet="$(QUIET)"; \
	[ -z "$$dirs" ] && { printf "$(DIM)No templates to run '%s'.$(RESET)\n" "$$cmd"; exit 0; }; \
	eval "$$SH_LANG_HELPERS"; \
	passed=0; failed=0; failed_dirs=""; \
	for dir in $$dirs; do \
	  runner=$$(runner_of "$$dir") || { printf "  $(RED)✗$(RESET) %s: no recognized runner\n" "$$dir"; failed=$$((failed+1)); continue; }; \
	  [ -z "$$quiet" ] && printf "\n$(BOLD)▶ %s$(RESET) $(DIM)(%s · %s)$(RESET)\n" "$$dir" "$$(lang_of "$$dir")" "$$cmd"; \
	  if (cd "$$dir" && $$runner "$$cmd" $$args); then \
	    passed=$$((passed+1)); \
	  else \
	    failed=$$((failed+1)); failed_dirs="$$failed_dirs $$dir"; \
	  fi; \
	done; \
	[ -n "$$quiet" ] && [ $$failed -eq 0 ] && exit 0; \
	printf "\n"; \
	if [ $$failed -gt 0 ]; then \
	  printf "$(RED)FAIL$(RESET) %d passed, %d failed\n" "$$passed" "$$failed"; \
	  for d in $$failed_dirs; do printf "  Retry: $(BOLD)(cd %s && %s %s)$(RESET)\n" "$$d" "$$(runner_of "$$d")" "$$cmd"; done; \
	  exit 1; \
	fi; \
	printf "$(GREEN)OK$(RESET) %d passed\n" "$$passed"

.PHONY: help
help: ## Show this message
	@printf "$(BOLD)harness-templates$(RESET) — repo-level gates.\n\n"
	@awk 'BEGIN { FS = ":.*## " } \
	     /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
