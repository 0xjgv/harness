# Monorepo harness — dispatches check / pre-commit / ci to each subproject's
# own harness. No lint, format, or test logic lives here. Each subproject
# keeps its own zero-dep harness.{ts,py,go,rs}; the Makefile is pure routing.
#
# Discovery: any top-level directory containing a recognized harness file is a
# subproject. Supported: harness.ts (bun), harness.py (python), harness.go (go),
# Cargo.toml (rust).

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ── Parallelism (opt-in) ───────────────────────────────────────────
# PARALLEL=1 → fan out subprojects with xargs -P. Output is buffered per
# subproject and dumped in deterministic order when each finishes. Keep off
# for CI logs and agent-visible runs (post-edit forces sequential anyway).
PARALLEL ?=
JOBS ?= $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)

# ── Colors (expand inline; printf interprets \033 at runtime) ──────
BLUE  := \033[34m
GREEN := \033[32m
RED   := \033[31m
DIM   := \033[2m
BOLD  := \033[1m
CYAN  := \033[36m
RESET := \033[0m

# ── Subproject discovery ───────────────────────────────────────────
BUN_DIRS  := $(patsubst %/harness.ts,%,$(wildcard */harness.ts))
PY_DIRS   := $(patsubst %/harness.py,%,$(wildcard */harness.py))
GO_DIRS   := $(patsubst %/harness.go,%,$(wildcard */harness.go))
RUST_DIRS := $(patsubst %/Cargo.toml,%,$(wildcard */Cargo.toml))
SUBPROJECTS := $(sort $(BUN_DIRS) $(PY_DIRS) $(GO_DIRS) $(RUST_DIRS))

# ── Shared shell helpers ───────────────────────────────────────────
# Single filename → {lang, runner} dispatch table. Recipes source it via
# `eval "$$SH_LANG_HELPERS"`. Adding a language: add a case to each function.
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

# `filter_dirs <cmd>` runs <cmd> (a string eval'd by the shell), interprets its
# stdout as newline-separated paths, and echoes the subset of $(SUBPROJECTS)
# that contain any of those paths. Empty output if <cmd> fails (e.g. not a git
# repo) or produces no matches. Building blocks: dirty_dirs (working-tree +
# untracked) and staged_dirs (index-only).
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

##@ Daily

.PHONY: check
check: ## Run check across all subprojects (auto-fix)
	@$(MAKE) --no-print-directory _root_agents_drift || exit 1
	@$(MAKE) --no-print-directory _run CMD=check DIRS="$(SUBPROJECTS)"

.PHONY: check-dirty
check-dirty: ## Run check only in subprojects with working-tree changes
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; \
	dirs=$$(dirty_dirs); \
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)Nothing to check.$(RESET)\n"; exit 0; \
	fi; \
	$(MAKE) --no-print-directory _run CMD=check DIRS="$$dirs"

.PHONY: pre-commit
pre-commit: ## Staged-only checks across affected subprojects
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; \
	dirs=$$(staged_dirs); \
	staged_root=$$(git diff --cached --name-only --diff-filter=d | grep -E '^(CLAUDE|AGENTS)\.md$$' || true); \
	if [ -n "$$staged_root" ]; then \
	  $(MAKE) --no-print-directory _root_agents_drift || exit 1; \
	fi; \
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)No staged files in known subprojects.$(RESET)\n"; exit 0; \
	fi; \
	$(MAKE) --no-print-directory _run CMD=pre-commit DIRS="$$dirs"

.PHONY: ci
ci: ## Read-only gate across all subprojects
	@$(MAKE) --no-print-directory _run CMD=ci DIRS="$(SUBPROJECTS)"

.PHONY: pre-push
pre-push: ## Read-only push gate across all subprojects (lint, format check, acceptance, arch)
	@$(MAKE) --no-print-directory _run CMD=pre-push DIRS="$(SUBPROJECTS)"

.PHONY: test
test: ## Run tests only, all subprojects
	@$(MAKE) --no-print-directory _run CMD=test DIRS="$(SUBPROJECTS)"

.PHONY: crap
crap: ## CRAP gate (advisory) across all subprojects; forward flags via ARGS
	@$(MAKE) --no-print-directory _run CMD=crap DIRS="$(SUBPROJECTS)" ARGS="$(ARGS)"

.PHONY: complexity
complexity: ## Complexity gate across all subprojects; forward flags via ARGS
	@$(MAKE) --no-print-directory _run CMD=complexity DIRS="$(SUBPROJECTS)" ARGS="$(ARGS)"

.PHONY: agents-md-drift
agents-md-drift: ## Fail if any AGENTS.md differs from its CLAUDE.md (root + subprojects)
	@set -u -o pipefail; \
	if ! $(MAKE) --no-print-directory _root_agents_drift; then exit 1; fi; \
	$(MAKE) --no-print-directory _run CMD=agents-md-drift DIRS="$(SUBPROJECTS)"

.PHONY: sync-agents-md
sync-agents-md: ## Overwrite each AGENTS.md from its CLAUDE.md (root + subprojects)
	@set -u -o pipefail; \
	$(MAKE) --no-print-directory _root_agents_sync; \
	$(MAKE) --no-print-directory _run CMD=sync-agents-md DIRS="$(SUBPROJECTS)"

# ── Root-pair drift / sync (monorepo top-level AGENTS.md ↔ CLAUDE.md) ──
.PHONY: _root_agents_drift _root_agents_sync

_root_agents_drift:
	@set -u; \
	if [ ! -f CLAUDE.md ]; then \
	  printf "  $(RED)✗$(RESET) agents-md-drift (root): CLAUDE.md not found\n"; exit 1; \
	fi; \
	if [ ! -f AGENTS.md ]; then \
	  printf "  $(RED)✗$(RESET) agents-md-drift (root): AGENTS.md missing — run \`make sync-agents-md\`\n"; exit 1; \
	fi; \
	if cmp -s CLAUDE.md AGENTS.md; then \
	  printf "  $(GREEN)✓$(RESET) agents-md-drift (root)\n"; \
	else \
	  line=$$(diff CLAUDE.md AGENTS.md | grep -m1 -E '^[0-9]' || echo "?"); \
	  printf "  $(RED)✗$(RESET) agents-md-drift (root): AGENTS.md differs from CLAUDE.md (diff: %s) — run \`make sync-agents-md\`\n" "$$line"; \
	  exit 1; \
	fi

_root_agents_sync:
	@set -u; \
	if [ ! -f CLAUDE.md ]; then \
	  printf "  $(RED)✗$(RESET) sync-agents-md (root): CLAUDE.md not found\n"; exit 1; \
	fi; \
	cp CLAUDE.md AGENTS.md; \
	printf "  $(GREEN)✓$(RESET) sync-agents-md (root): AGENTS.md ← CLAUDE.md\n"

##@ Setup & misc

.PHONY: setup
setup: ## Install per-language deps (one-time)
	@if [ -z "$(SUBPROJECTS)" ]; then \
	  printf "$(DIM)No subprojects detected. See README for how to add one.$(RESET)\n"; exit 0; \
	fi
	@set -eu; for d in $(BUN_DIRS);  do printf "$(BOLD)▶ %s$(RESET) $(DIM)(bun install)$(RESET)\n"    "$$d"; (cd "$$d" && bun install); done
	@set -eu; for d in $(PY_DIRS);   do printf "$(BOLD)▶ %s$(RESET) $(DIM)(uv sync)$(RESET)\n"       "$$d"; (cd "$$d" && uv sync); done
	@set -eu; for d in $(GO_DIRS);   do printf "$(BOLD)▶ %s$(RESET) $(DIM)(go mod download)$(RESET)\n" "$$d"; (cd "$$d" && go mod download); done
	@set -eu; for d in $(RUST_DIRS); do printf "$(BOLD)▶ %s$(RESET) $(DIM)(cargo build)$(RESET)\n"    "$$d"; (cd "$$d" && cargo build); done

.PHONY: setup-hooks
setup-hooks: ## Install root pre-commit git hook and verify Claude/Codex Stop hook wiring
	@if [ ! -d .git ]; then \
	  printf "$(RED)Not a git repo.$(RESET) Run 'git init' first.\n"; exit 1; \
	fi
	@if [ -f .git/hooks/pre-commit ] && [ -z "$(FORCE)" ]; then \
	  printf "$(RED).git/hooks/pre-commit already exists.$(RESET) Re-run with FORCE=1 to overwrite.\n"; exit 1; \
	fi
	@mkdir -p .git/hooks
	@printf '#!/bin/sh\nexec make pre-commit\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@printf "$(GREEN)✓$(RESET) Installed .git/hooks/pre-commit → make pre-commit\n"
	@if [ -f .claude/settings.json ] && grep -q 'Stop' .claude/settings.json && grep -q 'stop-hook' .claude/settings.json; then \
	  printf "$(GREEN)✓$(RESET) Stop hook wiring (.claude/settings.json)\n"; \
	else \
	  printf "$(RED)⚠$(RESET) Missing Stop hook wiring: .claude/settings.json\n"; \
	fi
	@if [ -f .codex/hooks.json ] && grep -q 'Stop' .codex/hooks.json && grep -q 'stop-hook' .codex/hooks.json; then \
	  printf "$(GREEN)✓$(RESET) Stop hook wiring (.codex/hooks.json)\n"; \
	else \
	  printf "$(RED)⚠$(RESET) Missing Stop hook wiring: .codex/hooks.json\n"; \
	fi

.PHONY: bootstrap
bootstrap: setup setup-hooks ## Full first-time setup (setup + install hook)
	@printf "$(GREEN)✓ Bootstrap complete.$(RESET) Try: $(BOLD)make check$(RESET)\n"

.PHONY: clean
clean: ## Remove build artifacts and caches in each subproject
	@$(MAKE) --no-print-directory _run CMD=clean DIRS="$(SUBPROJECTS)"

.PHONY: list
list: ## Show detected subprojects
	@if [ -z "$(SUBPROJECTS)" ]; then \
	  printf "$(DIM)No subprojects detected.$(RESET)\n"; \
	  printf "Add one:  $(BOLD)cp -r ../harness-templates/bun/ ./web$(RESET)\n"; \
	  exit 0; \
	fi; \
	eval "$$SH_LANG_HELPERS"; \
	for d in $(SUBPROJECTS); do \
	  printf "  $(CYAN)%-18s$(RESET) %s\n" "$$d" "$$(lang_of "$$d")"; \
	done

.PHONY: help
help: ## Show this message
	@printf "$(BOLD)Monorepo harness$(RESET) — dispatches checks into subprojects.\n\n"
	@if [ -n "$(SUBPROJECTS)" ]; then \
	  eval "$$SH_LANG_HELPERS"; \
	  printf "$(DIM)Subprojects:$(RESET) "; first=1; \
	  for d in $(SUBPROJECTS); do \
	    [ $$first -eq 0 ] && printf ", "; \
	    printf "%s ($(CYAN)%s$(RESET))" "$$d" "$$(lang_of "$$d")"; \
	    first=0; \
	  done; \
	  printf "\n\n"; \
	else \
	  printf "$(DIM)No subprojects detected yet. See README to add one.$(RESET)\n\n"; \
	fi
	@awk 'BEGIN { FS = ":.*## " } \
	     /^##@ / { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
	     /^[a-zA-Z_%-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\n$(DIM)Scoped targets:$(RESET) $(BOLD)check-<subproject>$(RESET), $(BOLD)ci-<subproject>$(RESET), $(BOLD)pre-push-<subproject>$(RESET), $(BOLD)test-<subproject>$(RESET), $(BOLD)crap-<subproject>$(RESET), $(BOLD)complexity-<subproject>$(RESET)\n"
	@printf "$(DIM)Forward flags:$(RESET)  $(BOLD)ARGS=\"--enforce --max=50\" make crap$(RESET)\n"
	@printf "$(DIM)Verbose:$(RESET)        $(BOLD)VERBOSE=1 make check$(RESET)\n"
	@printf "$(DIM)Parallel:$(RESET)       $(BOLD)PARALLEL=1 make check$(RESET) $(DIM)(buffers per-subproject output)$(RESET)\n"

# ── Scoped pattern rules ──────────────────────────────────────────
# make check-api / ci-web / test-ml / pre-commit-api

check-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=check DIR="$*"

ci-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=ci DIR="$*"

pre-push-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=pre-push DIR="$*"

test-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=test DIR="$*"

crap-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=crap DIR="$*" ARGS="$(ARGS)"

complexity-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=complexity DIR="$*" ARGS="$(ARGS)"

agents-md-drift-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=agents-md-drift DIR="$*"

sync-agents-md-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=sync-agents-md DIR="$*"

pre-commit-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=pre-commit DIR="$*"

# ── Claude Code Stop hook target ──────────────────────────────────
# Invoked by .claude/settings.json on every Stop. Scopes to subprojects with
# working-tree changes so an N-subproject repo doesn't cold-start N toolchains
# per turn when Claude touched one file.
.PHONY: post-edit
post-edit:
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; \
	dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=post-edit DIRS="$$dirs" QUIET=1 PARALLEL=

.PHONY: stop-hook
stop-hook:
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; \
	dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	printf "\n=== Stop Hook Checks ===\n"; \
	$(MAKE) --no-print-directory _run CMD=stop-hook DIRS="$$dirs" PARALLEL=

# ── Internal dispatch ─────────────────────────────────────────────

.PHONY: _run _scoped_run

_scoped_run:
	@set -u; \
	case " $(SUBPROJECTS) " in \
	  *" $(DIR) "*) ;; \
	  *) printf "$(RED)Unknown subproject:$(RESET) %s\n" "$(DIR)"; \
	     printf "$(DIM)Available:$(RESET) %s\n" "$(SUBPROJECTS)"; \
	     exit 1 ;; \
	esac; \
	$(MAKE) --no-print-directory _run CMD="$(CMD)" DIRS="$(DIR)" ARGS="$(ARGS)"

_run:
	@set -u -o pipefail; \
	dirs="$(DIRS)"; cmd="$(CMD)"; vf="$(if $(VERBOSE),--verbose)"; quiet="$(QUIET)"; args="$(ARGS)"; \
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)No subprojects to run '%s'.$(RESET)\n" "$$cmd"; \
	  exit 0; \
	fi; \
	eval "$$SH_LANG_HELPERS"; \
	run_one() { \
	  local dir="$$1" cmd="$$2" vf="$$3" quiet="$$4" args="$$5" runner; \
	  if ! runner=$$(runner_of "$$dir"); then \
	    printf "$(RED)✗ %s$(RESET) $(DIM)(no recognized harness — skipped)$(RESET)\n" "$$dir"; \
	    return 1; \
	  fi; \
	  [ -z "$$quiet" ] && printf "\n$(BOLD)▶ %s$(RESET) $(DIM)($(CYAN)%s$(RESET)$(DIM) · %s)$(RESET)\n" "$$dir" "$$(lang_of "$$dir")" "$$cmd"; \
	  ( cd "$$dir" && $$runner $$cmd $$vf $$args ); \
	}; \
	passed=0; failed=0; failed_dirs=""; t_start=$$(date +%s); \
	if [ -n "$(PARALLEL)" ]; then \
	  tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	  export -f run_one lang_of runner_of; \
	  printf '%s\n' $$dirs | xargs -n1 -P$(JOBS) -I{} bash -c ' \
	    run_one "$$1" "$$2" "$$3" "$$4" "$$5" >"$$6/$$1.out" 2>&1; \
	    echo $$? >"$$6/$$1.rc"' _ {} "$$cmd" "$$vf" "$$quiet" "$$args" "$$tmp"; \
	  for dir in $$dirs; do \
	    [ -z "$$quiet" ] && cat "$$tmp/$$dir.out"; \
	    rc=$$(cat "$$tmp/$$dir.rc" 2>/dev/null || echo 1); \
	    if [ "$$rc" = 0 ]; then passed=$$((passed+1)); \
	    else failed=$$((failed+1)); failed_dirs="$$failed_dirs $$dir"; fi; \
	  done; \
	else \
	  for dir in $$dirs; do \
	    if run_one "$$dir" "$$cmd" "$$vf" "$$quiet" "$$args"; then \
	      passed=$$((passed+1)); \
	    else \
	      failed=$$((failed+1)); failed_dirs="$$failed_dirs $$dir"; \
	    fi; \
	  done; \
	fi; \
	t_elapsed=$$(( $$(date +%s) - t_start )); \
	if [ -n "$$quiet" ] && [ $$failed -eq 0 ]; then exit 0; fi; \
	printf "\n"; \
	if [ $$failed -gt 0 ]; then \
	  printf "$(RED)FAIL$(RESET) %d passed, %d failed $(DIM)(%ds)$(RESET)\n" "$$passed" "$$failed" "$$t_elapsed"; \
	  case "$$cmd" in \
	    check|ci|test|pre-commit|pre-push) \
	      for d in $$failed_dirs; do \
	        printf "  Retry just this one:  $(BOLD)make %s-%s$(RESET)\n" "$$cmd" "$$d"; \
	      done ;; \
	  esac; \
	  exit 1; \
	fi; \
	printf "$(GREEN)OK$(RESET) %d passed $(DIM)(%ds)$(RESET)\n" "$$passed" "$$t_elapsed"
