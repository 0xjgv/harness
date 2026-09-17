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

# Agent hook dispatch. make turns every failed recipe into exit 2, so an exit
# code cannot carry the stop-hook contract (2 = findings, other = the tool
# failed) through a Makefile. `stop_dispatch <dirs...>` therefore always exits 0
# and answers as one hook JSON object on stdout, which Claude Code and Codex
# both read: a block with the findings, or a systemMessage for tool failures.
# Run from a terminal (stdin is a TTY) it prints the findings and exits 1.
# Go subprojects are built and run as ./harness: `go run` reports any failing
# program as exit 1, which would hide the findings exit code.
# `post_edit_hook` forwards a PostToolUse event to the template owning the file.
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
    local local_ref local_sha remote_ref remote_sha zero nb cand
    zero=0000000000000000000000000000000000000000
    while read -r local_ref local_sha remote_ref remote_sha; do
      [ -z "$$local_sha" ] && continue
      [ "$$local_sha" = "$$zero" ] && continue
      if [ "$$remote_sha" = "$$zero" ]; then
        nb=""; for cand in origin/main origin/master "$${HARNESS_ARCH_BASE:-}"; do
          [ -n "$$cand" ] && git rev-parse --verify -q "$$cand" >/dev/null 2>&1 && { nb=$$(git merge-base "$$cand" "$$local_sha" 2>/dev/null || true); break; }
        done
        if [ -n "$$nb" ]; then
          git diff --name-only "$$nb" "$$local_sha" 2>/dev/null || true
        else
          git diff-tree --no-commit-id --name-only -r "$$local_sha" 2>/dev/null || true
        fi
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

# Branch guard: refuse direct pushes to (or deletions of) main/master.
# Refs come from HARNESS_PRE_PUSH_REFS, else git pre-push stdin
# ("<local ref> <local sha> <remote ref> <remote sha>" per line) read with a
# 1s deadline (bash 3.2's read -t cannot tell timeout from EOF, so a background
# cat is reaped instead); partial input is a hard failure, no input falls back
# to the current branch.
define SH_BRANCH_GUARD
read_pre_push_refs() {
  local tmp pid i
  if [ -n "$${HARNESS_PRE_PUSH_REFS:-}" ]; then printf '%s\n' "$$HARNESS_PRE_PUSH_REFS"; return 0; fi
  [ -t 0 ] && return 0
  tmp=$$(mktemp); exec 3<&0; cat <&3 >"$$tmp" & pid=$$!
  for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$$pid" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$$pid" 2>/dev/null; then
    kill "$$pid" 2>/dev/null; wait "$$pid" 2>/dev/null
    [ -s "$$tmp" ] && printf '__INCOMPLETE__\n'
  else
    wait "$$pid" 2>/dev/null; cat "$$tmp"
  fi
  exec 3<&-; rm -f "$$tmp"
  return 0
}
branch_guard_targets() {
  local refs="$$1" local_ref local_sha remote_ref remote_sha records
  records=$$(printf '%s\n' "$$refs" | awk 'NF >= 4 { print $$3 }')
  if [ -z "$$records" ]; then
    git rev-parse --abbrev-ref HEAD 2>/dev/null || true
    return 0
  fi
  printf '%s\n' "$$records" | while read -r remote_ref; do
    case "$$remote_ref" in refs/heads/*) printf '%s\n' "$${remote_ref#refs/heads/}" ;; esac
  done
}
branch_guard() {
  local hit
  case "$$1" in *__INCOMPLETE__*)
    printf "  $(RED)✗$(RESET) Pre-push refs incomplete after 1s\n"; return 1 ;;
  esac
  hit=$$(branch_guard_targets "$$1" | grep -E '^(main|master)$$' | sort -u | paste -sd, -)
  if [ -z "$$hit" ]; then
    printf "  $(GREEN)✓$(RESET) Branch guard\n"
    return 0
  fi
  if [ "$${HARNESS_ALLOW_PROTECTED_PUSH:-}" = 1 ]; then
    printf "  $(GREEN)⚠$(RESET) Branch guard override: %s\n" "$$hit"
    return 0
  fi
  printf "  $(RED)✗$(RESET) Push targets protected branch: %s\n" "$$hit"
  printf "  ↳ fix: push a feature branch and open a PR; humans may set HARNESS_ALLOW_PROTECTED_PUSH=1\n"
  return 1
}
endef
export SH_BRANCH_GUARD
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

.PHONY: branch-guard
branch-guard: ## Refuse direct pushes to main/master (HARNESS_ALLOW_PROTECTED_PUSH=1 overrides)
	@set -u -o pipefail; eval "$$SH_BRANCH_GUARD"; \
	refs=$$(read_pre_push_refs); \
	branch_guard "$$refs" || exit 1

.PHONY: sync-derived
sync-derived: ## Sync AGENTS.md from an edited CLAUDE.md; deploy an edited skills/harness/
	@set -u; \
	if [ -n "$$(git status --porcelain -- CLAUDE.md)" ] && ! cmp -s CLAUDE.md AGENTS.md; then \
	  $(MAKE) --no-print-directory sync-agents-md; \
	fi; \
	if [ -n "$$(git status --porcelain -- skills/harness)" ]; then \
	  $(MAKE) --no-print-directory sync-skills; \
	fi

.PHONY: post-edit
post-edit: sync-derived ## Sync derived docs/skills and format dirty templates
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; dirs=$$(dirty_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=post-edit DIRS="$$dirs" QUIET=1

.PHONY: post-edit-hook
post-edit-hook: ## Claude PostToolUse hook: fix + format the edited file in its template
	@set -u; eval "$$SH_LANG_HELPERS"; eval "$$SH_HOOK_DISPATCH"; post_edit_hook

.PHONY: stop-hook
stop-hook: ## Agent Stop hook: sync derived docs, then dirty templates' stop-hooks as one JSON answer
	@$(MAKE) --no-print-directory sync-derived >&2
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; eval "$$SH_LANG_HELPERS"; eval "$$SH_HOOK_DISPATCH"; \
	stop_dispatch $$(dirty_dirs)

.PHONY: pre-commit
pre-commit: ## Root git pre-commit hook
	@set -u -o pipefail; eval "$$SH_ARCH_CONFIG_GUARD"; arch_config_guard 1 1 0
	@set -u; \
	if [ -n "$$(git diff --cached --name-only -- CLAUDE.md)" ] && ! cmp -s CLAUDE.md AGENTS.md; then \
	  $(MAKE) --no-print-directory sync-agents-md && git add AGENTS.md; \
	fi
	@$(MAKE) --no-print-directory agents-md-drift
	@set -u -o pipefail; eval "$$SH_FILTER_DIRS"; dirs=$$(staged_dirs); \
	[ -z "$$dirs" ] && exit 0; \
	$(MAKE) --no-print-directory _run CMD=pre-commit DIRS="$$dirs"

.PHONY: pre-push
pre-push: ## Root git pre-push hook: branch guard first, then drift, arch guard, templates
	@set -u -o pipefail; eval "$$SH_BRANCH_GUARD"; eval "$$SH_ARCH_CONFIG_GUARD"; \
	refs=$$(read_pre_push_refs); \
	branch_guard "$$refs" || exit 1; \
	$(MAKE) --no-print-directory agents-md-drift; \
	$(MAKE) --no-print-directory skills-drift; \
	printf '%s\n' "$$refs" | arch_config_guard 0 0 1 || exit 1; \
	HARNESS_PRE_PUSH_REFS="$$refs" $(MAKE) --no-print-directory _run CMD=pre-push DIRS="$(SUBPROJECTS)"

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
	@if [ -f .claude/settings.json ] && grep -q 'PostToolUse' .claude/settings.json && grep -q 'post-edit-hook' .claude/settings.json; then \
	  printf "  $(GREEN)✓$(RESET) PostToolUse hook wiring (.claude/settings.json)\n"; \
	else \
	  printf "  $(RED)⚠$(RESET) Missing PostToolUse hook wiring: .claude/settings.json\n"; \
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
# A git hook in a linked worktree exports GIT_DIR, which tells git the current
# directory is the top of the work tree; after `cd <subproject>` that makes
# `--relative` and `--show-prefix` wrong. Unset it (git rediscovers the same
# repo from the cwd; GIT_INDEX_FILE stays, so pre-commit still sees the index).
_run:
	@set -u -o pipefail; unset GIT_DIR GIT_WORK_TREE; \
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
