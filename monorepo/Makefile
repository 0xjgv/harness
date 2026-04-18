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
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)No staged files in known subprojects.$(RESET)\n"; exit 0; \
	fi; \
	$(MAKE) --no-print-directory _run CMD=pre-commit DIRS="$$dirs"

.PHONY: ci
ci: ## Read-only gate across all subprojects
	@$(MAKE) --no-print-directory _run CMD=ci DIRS="$(SUBPROJECTS)"

.PHONY: test
test: ## Run tests only, all subprojects
	@$(MAKE) --no-print-directory _run CMD=test DIRS="$(SUBPROJECTS)"

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
setup-hooks: ## Install root pre-commit git hook
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
	@printf "\n$(DIM)Scoped targets:$(RESET) $(BOLD)check-<subproject>$(RESET), $(BOLD)ci-<subproject>$(RESET), $(BOLD)test-<subproject>$(RESET)\n"
	@printf "$(DIM)Verbose:$(RESET)        $(BOLD)VERBOSE=1 make check$(RESET)\n"
	@printf "$(DIM)Parallel:$(RESET)       $(BOLD)PARALLEL=1 make check$(RESET) $(DIM)(buffers per-subproject output)$(RESET)\n"

# ── Scoped pattern rules ──────────────────────────────────────────
# make check-api / ci-web / test-ml / pre-commit-api

check-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=check DIR="$*"

ci-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=ci DIR="$*"

test-%:
	@$(MAKE) --no-print-directory _scoped_run CMD=test DIR="$*"

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
	$(MAKE) --no-print-directory _run CMD="$(CMD)" DIRS="$(DIR)"

_run:
	@set -u -o pipefail; \
	dirs="$(DIRS)"; cmd="$(CMD)"; vf="$(if $(VERBOSE),--verbose)"; quiet="$(QUIET)"; \
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)No subprojects to run '%s'.$(RESET)\n" "$$cmd"; \
	  exit 0; \
	fi; \
	eval "$$SH_LANG_HELPERS"; \
	run_one() { \
	  local dir="$$1" cmd="$$2" vf="$$3" quiet="$$4" runner; \
	  if ! runner=$$(runner_of "$$dir"); then \
	    printf "$(RED)✗ %s$(RESET) $(DIM)(no recognized harness — skipped)$(RESET)\n" "$$dir"; \
	    return 1; \
	  fi; \
	  [ -z "$$quiet" ] && printf "\n$(BOLD)▶ %s$(RESET) $(DIM)($(CYAN)%s$(RESET)$(DIM) · %s)$(RESET)\n" "$$dir" "$$(lang_of "$$dir")" "$$cmd"; \
	  ( cd "$$dir" && $$runner $$cmd $$vf ); \
	}; \
	passed=0; failed=0; failed_dirs=""; t_start=$$(date +%s); \
	if [ -n "$(PARALLEL)" ]; then \
	  tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	  export -f run_one lang_of runner_of; \
	  printf '%s\n' $$dirs | xargs -n1 -P$(JOBS) -I{} bash -c ' \
	    run_one "$$1" "$$2" "$$3" "$$4" >"$$5/$$1.out" 2>&1; \
	    echo $$? >"$$5/$$1.rc"' _ {} "$$cmd" "$$vf" "$$quiet" "$$tmp"; \
	  for dir in $$dirs; do \
	    [ -z "$$quiet" ] && cat "$$tmp/$$dir.out"; \
	    rc=$$(cat "$$tmp/$$dir.rc" 2>/dev/null || echo 1); \
	    if [ "$$rc" = 0 ]; then passed=$$((passed+1)); \
	    else failed=$$((failed+1)); failed_dirs="$$failed_dirs $$dir"; fi; \
	  done; \
	else \
	  for dir in $$dirs; do \
	    if run_one "$$dir" "$$cmd" "$$vf" "$$quiet"; then \
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
	    check|ci|test|pre-commit) \
	      for d in $$failed_dirs; do \
	        printf "  Retry just this one:  $(BOLD)make %s-%s$(RESET)\n" "$$cmd" "$$d"; \
	      done ;; \
	  esac; \
	  exit 1; \
	fi; \
	printf "$(GREEN)OK$(RESET) %d passed $(DIM)(%ds)$(RESET)\n" "$$passed" "$$t_elapsed"
