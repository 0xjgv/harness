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

# Agent hook dispatch. make turns every failed recipe into exit 2, so an exit
# code cannot carry the stop-hook contract (2 = findings, other = the tool
# failed) through a Makefile. `stop_dispatch <dirs...>` therefore always exits 0
# and answers as one hook JSON object on stdout, which Claude Code and Codex
# both read: a block with the findings, or a systemMessage for tool failures.
# Run from a terminal (stdin is a TTY) it prints the findings and exits 1.
# Go subprojects are built and run as ./harness: `go run` reports any failing
# program as exit 1, which would hide the findings exit code.
# `post_edit_hook` forwards a PostToolUse event to the subproject owning the file.
define SH_HOOK_DISPATCH
json_escape() {
  awk 'BEGIN { ORS = "" }
    {
      gsub(/\033\[[0-9;]*[A-Za-z]/, "")
      gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t")
      gsub(/[\001-\010\013-\037]/, "")
      if (NR > 1) print "\\n"
      print
    }'
}
prefix_lines() {
  sed -E -e "s|^([^[:space:]]+:[0-9]+:)|$$1/\1|" -e t -e "s|^|$$1: |"
}
stop_dispatch() {
  local tmp dir runner rc
  tmp=$$(mktemp -d) || return 1
  if [ -t 0 ]; then : >"$$tmp/in"; else cat >"$$tmp/in"; fi
  : >"$$tmp/block"; : >"$$tmp/warn"
  for dir in "$${@:-}"; do
    [ -n "$$dir" ] || continue
    runner=$$(runner_of "$$dir") || continue
    if [ "$$(lang_of "$$dir")" = go ]; then
      runner=./harness
      if ! (cd "$$dir" && go build -o harness harness.go) >/dev/null 2>"$$tmp/err"; then
        prefix_lines "$$dir" <"$$tmp/err" >>"$$tmp/warn"
        continue
      fi
    fi
    (cd "$$dir" && $$runner stop-hook) <"$$tmp/in" >/dev/null 2>"$$tmp/err"
    rc=$$?
    case "$$rc" in
      0) ;;
      2) prefix_lines "$$dir" <"$$tmp/err" >>"$$tmp/block" ;;
      *) [ -s "$$tmp/err" ] || echo "stop-hook exited $$rc" >"$$tmp/err"
         prefix_lines "$$dir" <"$$tmp/err" >>"$$tmp/warn" ;;
    esac
  done
  if [ -t 0 ]; then
    cat "$$tmp/block" "$$tmp/warn" >&2
    [ -s "$$tmp/block" ] && { rm -rf "$$tmp"; return 1; }
    rm -rf "$$tmp"; return 0
  fi
  if [ -s "$$tmp/block" ]; then
    printf '{"decision":"block","reason":"%s"' "$$(json_escape <"$$tmp/block")"
  else
    printf '{"continue":true'
  fi
  [ -s "$$tmp/warn" ] && printf ',"systemMessage":"%s"' "$$(json_escape <"$$tmp/warn")"
  printf '}\n'
  rm -rf "$$tmp"
}
post_edit_hook() {
  local event file fdir rel dir runner top
  event=$$(cat)
  file=$$(printf '%s' "$$event" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
  [ -n "$$file" ] || return 0
  top=$$(pwd -P)
  fdir=$$(cd "$$(dirname "$$file")" 2>/dev/null && pwd -P) || return 0
  rel="$${fdir#"$$top"/}"
  [ "$$rel" != "$$fdir" ] || return 0
  dir="$${rel%%/*}"
  case " $(SUBPROJECTS) " in *" $$dir "*) ;; *) return 0 ;; esac
  runner=$$(runner_of "$$dir") || return 0
  printf '%s' "$$event" | (cd "$$dir" && $$runner post-edit --hook) 2>/dev/null || true
}
endef
export SH_HOOK_DISPATCH

define SH_ARCH_CONFIG_GUARD
arch_config_filter() {
  grep -E '(^|/)(\.importlinter|\.dependency-cruiser\.json|\.go-arch-lint\.yml|arch\.toml)$$' | sort -u
}
arch_base_ref() {
  local ref
  for ref in origin/main origin/master "$${HARNESS_ARCH_BASE:-}"; do
    [ -z "$$ref" ] && continue
    git rev-parse --verify --quiet "$$ref" >/dev/null 2>&1 && { printf '%s' "$$ref"; return 0; }
  done
  return 1
}
arch_config_new_branch_paths() {
  local local_sha="$$1" base="" mb=""
  base=$$(arch_base_ref) || base=""
  [ -n "$$base" ] && mb=$$(git merge-base "$$base" "$$local_sha" 2>/dev/null || true)
  if [ -n "$$mb" ]; then
    git diff --name-only "$$mb" "$$local_sha" 2>/dev/null || true
  else
    git diff-tree --no-commit-id --name-only -r "$$local_sha" 2>/dev/null || true
  fi
}
arch_config_changed_paths() {
  local staged="$$1" include_pre_push="$$2" base=""
  if [ "$$staged" = 1 ]; then
    git diff --cached --name-only 2>/dev/null || true
    return 0
  fi
  git diff --name-only 2>/dev/null || true
  git diff --cached --name-only 2>/dev/null || true
  git ls-files --others --exclude-standard 2>/dev/null || true
  if [ -n "$${HARNESS_ARCH_BASE:-}" ]; then
    base="$$HARNESS_ARCH_BASE"
  elif [ -n "$${GITHUB_BASE_REF:-}" ]; then
    base="origin/$$GITHUB_BASE_REF"
  fi
  if [ -n "$$base" ] && git rev-parse --verify "$$base" >/dev/null 2>&1; then
    git diff --name-only "$$base...HEAD" 2>/dev/null || true
  fi
  if [ "$$include_pre_push" = 1 ] && [ ! -t 0 ]; then
    local local_ref local_sha remote_ref remote_sha zero
    zero=0000000000000000000000000000000000000000
    while read -r local_ref local_sha remote_ref remote_sha; do
      [ -z "$$local_sha" ] && continue
      [ "$$local_sha" = "$$zero" ] && continue
      if [ "$$remote_sha" = "$$zero" ]; then
        arch_config_new_branch_paths "$$local_sha"
      else
        git diff --name-only "$$remote_sha" "$$local_sha" 2>/dev/null || true
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
  changed=$$(printf '%s\n' "$$changed" | paste -sd ', ' -)
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

define SH_BRANCH_GUARD
# Pre-push refs source, resolved once per process: HARNESS_PRE_PUSH_REFS (set by a
# dispatcher for child harnesses), else stdin when it is not a tty. The stdin read has
# a 1s deadline covering the whole read: an idle pipe yields no refs (manual run), but
# a pipe that delivered data and stalled yields the line `__INCOMPLETE__` — never a
# current-branch guess. (macOS bash 3.2 cannot tell a `read -t` timeout from EOF, hence
# the background `cat` + poll.)
read_pre_push_refs() {
  local tmp pid i
  if [ -n "$${HARNESS_PRE_PUSH_REFS:-}" ]; then
    printf '%s\n' "$$HARNESS_PRE_PUSH_REFS"
    return 0
  fi
  [ -t 0 ] && return 0
  tmp=$$(mktemp); exec 3<&0; cat <&3 >"$$tmp" & pid=$$!
  for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$$pid" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$$pid" 2>/dev/null; then
    kill "$$pid" 2>/dev/null; wait "$$pid" 2>/dev/null
    [ -s "$$tmp" ] && printf '__INCOMPLETE__\n'
  else
    wait "$$pid" 2>/dev/null; cat "$$tmp"
  fi
  exec 3<&-; rm -f "$$tmp"; return 0
}
refs_incomplete() {
  printf "  $(RED)✗$(RESET) Pre-push refs incomplete after 1s\n"
  printf "  ↳ fix: rerun from the git pre-push hook, or pass HARNESS_PRE_PUSH_REFS\n"
  return 1
}
branch_guard() {
  local r_ref target="" branch="" refs="" records=""
  if [ ! -t 0 ]; then
    refs=$$(cat)
    case "$$refs" in *__INCOMPLETE__*) refs_incomplete; return 1 ;; esac
    # Only a fully-populated "local-ref local-sha remote-ref remote-sha" line
    # counts as a parsed record. Malformed/short lines (e.g. HARNESS_PRE_PUSH_REFS
    # set to garbage) must not suppress the current-branch fallback below.
    records=$$(printf '%s\n' "$$refs" | awk 'NF >= 4 { print $$3 }')
  fi
  if [ -z "$$records" ]; then
    branch=$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
    case "$$branch" in main|master) target="$$branch" ;; esac
  else
    while read -r r_ref; do
      case "$$r_ref" in
        refs/heads/main|refs/heads/master) target="$${r_ref#refs/heads/}" ;;
      esac
    done <<< "$$records"
  fi
  if [ -z "$$target" ]; then
    printf "  $(GREEN)✓$(RESET) Branch guard\n"
    return 0
  fi
  if [ "$${HARNESS_ALLOW_PROTECTED_PUSH:-}" = 1 ]; then
    printf "  $(GREEN)⚠$(RESET) Branch guard override: %s\n" "$$target"
    return 0
  fi
  printf "  $(RED)✗$(RESET) Push targets protected branch: %s\n" "$$target"
  printf "  ↳ fix: push a feature branch and open a PR; humans may set HARNESS_ALLOW_PROTECTED_PUSH=1\n"
  return 1
}
endef
export SH_BRANCH_GUARD

##@ Daily

.PHONY: check
check: ## Run check across all subprojects (auto-fix)
	@$(MAKE) --no-print-directory _root_agents_drift || exit 1
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 1 0
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
	eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 1 1 0; \
	dirs=$$(staged_dirs); \
	staged_root=$$(git diff --cached --name-only --diff-filter=d | grep -E '^(CLAUDE|AGENTS)\.md$$' || true); \
	if printf '%s\n' "$$staged_root" | grep -qx 'CLAUDE.md' && ! cmp -s CLAUDE.md AGENTS.md; then \
	  $(MAKE) --no-print-directory _root_agents_sync && git add AGENTS.md || exit 1; \
	fi; \
	if [ -n "$$staged_root" ]; then \
	  $(MAKE) --no-print-directory _root_agents_drift || exit 1; \
	fi; \
	if [ -z "$$dirs" ]; then \
	  printf "$(DIM)No staged files in known subprojects.$(RESET)\n"; exit 0; \
	fi; \
	$(MAKE) --no-print-directory _run CMD=pre-commit DIRS="$$dirs"

.PHONY: ci
ci: ## Read-only gate across all subprojects
	@$(MAKE) --no-print-directory _root_agents_drift || exit 1
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 0 0 0
	@$(MAKE) --no-print-directory _run CMD=ci DIRS="$(SUBPROJECTS)"

.PHONY: pre-push
pre-push: ## Read-only push gate across all subprojects (branch guard, lint, format check, acceptance, arch, agents-md drift)
	@set -u -o pipefail; \
	eval "$$SH_BRANCH_GUARD"; \
	refs=$$(read_pre_push_refs); \
	case "$$refs" in *__INCOMPLETE__*) refs_incomplete; exit 1 ;; esac; \
	printf '%s\n' "$$refs" | branch_guard || exit 1; \
	eval "$$SH_ARCH_CONFIG_GUARD"; \
	rc=0; \
	printf '%s\n' "$$refs" | arch_config_guard 0 0 1 || rc=1; \
	$(MAKE) --no-print-directory _root_agents_drift || rc=1; \
	[ $$rc -eq 0 ] || exit 1; \
	[ -n "$$refs" ] && export HARNESS_PRE_PUSH_REFS="$$refs"; \
	$(MAKE) --no-print-directory _run CMD=pre-push DIRS="$(SUBPROJECTS)"

.PHONY: branch-guard
branch-guard: ## Refuse pushes to (or deletions of) main/master; HARNESS_ALLOW_PROTECTED_PUSH=1 overrides
	@set -u -o pipefail; eval "$$SH_BRANCH_GUARD"; \
	refs=$$(read_pre_push_refs); \
	printf '%s\n' "$$refs" | branch_guard

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

.PHONY: arch-config-guard
arch-config-guard: ## Block unreviewed arch config changes; pass ARGS=--warn for advisory mode
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; \
	warn=0; staged=0; \
	case " $(ARGS) " in *" --warn "*) warn=1 ;; esac; \
	case " $(ARGS) " in *" --staged "*) staged=1 ;; esac; \
	arch_config_guard "$$staged" "$$warn" 0

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
setup-hooks: ## Install root pre-commit + pre-push git hooks and verify Claude/Codex Stop hook wiring
	@if ! env -u GIT_DIR -u GIT_WORK_TREE git rev-parse --git-dir >/dev/null 2>&1; then \
	  printf "$(RED)Not a git repo.$(RESET) Run 'git init' first.\n"; exit 1; \
	fi
	@set -eu; for name in pre-commit pre-push; do \
	  hook=$$(env -u GIT_DIR -u GIT_WORK_TREE git rev-parse --git-path "hooks/$$name"); \
	  mkdir -p "$$(dirname "$$hook")"; \
	  printf '#!/bin/sh\nexec make %s\n' "$$name" > "$$hook"; \
	  chmod +x "$$hook"; \
	  printf "$(GREEN)✓$(RESET) Installed %s → make %s\n" "$$hook" "$$name"; \
	done
	@if [ -f .claude/settings.json ] && grep -q 'Stop' .claude/settings.json && grep -q 'stop-hook' .claude/settings.json; then \
	  printf "$(GREEN)✓$(RESET) Stop hook wiring (.claude/settings.json)\n"; \
	else \
	  printf "$(RED)⚠$(RESET) Missing Stop hook wiring: .claude/settings.json\n"; \
	fi
	@if [ -f .claude/settings.json ] && grep -q 'PostToolUse' .claude/settings.json && grep -q 'post-edit-hook' .claude/settings.json; then \
	  printf "$(GREEN)✓$(RESET) PostToolUse hook wiring (.claude/settings.json)\n"; \
	else \
	  printf "$(RED)⚠$(RESET) Missing PostToolUse hook wiring: .claude/settings.json\n"; \
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

# ── Agent hook targets ────────────────────────────────────────────
# Stop (.claude/settings.json, .codex/hooks.json) scopes to subprojects with
# working-tree changes so an N-subproject repo doesn't cold-start N toolchains
# per turn when the agent touched one file, and answers as one hook JSON
# object (see SH_HOOK_DISPATCH). PostToolUse forwards each Edit/Write to the
# subproject that owns the file.
.PHONY: post-edit
post-edit: _root_agents_autosync
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; \
	dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=post-edit DIRS="$$dirs" QUIET=1 PARALLEL=

.PHONY: post-edit-hook
post-edit-hook:
	@set -u; eval "$$SH_LANG_HELPERS"; eval "$$SH_HOOK_DISPATCH"; post_edit_hook

.PHONY: stop-hook
stop-hook:
	@$(MAKE) --no-print-directory _root_agents_autosync >&2
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; eval "$$SH_LANG_HELPERS"; eval "$$SH_HOOK_DISPATCH"; \
	stop_dispatch $$(dirty_dirs)

# Root CLAUDE.md is canonical: an uncommitted edit to it refreshes AGENTS.md.
.PHONY: _root_agents_autosync
_root_agents_autosync:
	@set -u; \
	if [ -f CLAUDE.md ] && [ -n "$$(git status --porcelain -- CLAUDE.md 2>/dev/null)" ] && ! cmp -s CLAUDE.md AGENTS.md; then \
	  $(MAKE) --no-print-directory _root_agents_sync; \
	fi

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
