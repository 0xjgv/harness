#!/usr/bin/env bash
# scripts/parity-gate.sh — cross-template command-surface PARITY GATE.
#
# WHY: python/, bun/, go/, rust/ each ship an independent, zero-dependency
# harness runner implementing a deliberately DUPLICATED command surface (see
# root CLAUDE.md — "there is no cross-template abstraction for this, each
# runner is stdlib/runtime-only by design"). Nothing else in this repo
# checks that the four surfaces stay in sync with each other, or that a
# template's own Makefile / CLAUDE.md / (bun) package.json agree with what
# its runner actually dispatches. An audit found six such parity-drift bugs
# (e.g. bun/CLAUDE.md documented `bun run audit` with no matching
# package.json script, so on macOS it silently ran /usr/sbin/audit instead
# of the harness). This script is the gate that would have caught them.
#
# WHAT THIS CHECKS (static extraction only):
#   1. Command surface, per template (python/bun/go/rust):
#      a. every command the runner dispatches also appears in that
#         template's Makefile HARNESS_TARGETS, and vice versa (unless
#         allowlisted below) — a mismatch means `make <cmd>` or the runner
#         itself would silently 404.
#      b. every command a template's CLAUDE.md documents via a runner
#         invocation form (`uv run harness <cmd>`, `bun harness.ts <cmd>`,
#         `bun run <cmd>`, `go run harness.go <cmd>`, `cargo harness <cmd>`,
#         `make <cmd>`) actually exists in that template's dispatch table.
#      c. bun only: every `bun run <cmd>` form documented in bun/CLAUDE.md
#         has a matching bun/package.json "scripts" entry — this is the
#         exact shape of the audit/BSM-audit bug above.
#   2. Core command coverage: all four templates must expose the 20 command
#      names every template contract requires (check, pre-commit, ...).
#      Non-negotiable — never allowlist-exempt.
#   3. Cross-template surface parity: beyond the 20 core commands, every
#      command any one template exposes must exist in all four, UNLESS the
#      (template, command) pair is declared in the ALLOWLIST below. This is
#      what makes e.g. "go/rust ship no deadcode command" a reviewable
#      one-line decision instead of silent, undetected drift.
#   4. Lizard pin parity: the complexity/CRAP/duplication gates in all four
#      templates run the same lizard release. The pin is a literal in each
#      runner (`lizard@<ver>` in bun/harness.ts, go/harness.go,
#      rust/harness.rs) and a dev dependency in python/pyproject.toml
#      (`lizard==<ver>`); every file must pin exactly one version and all
#      four must agree, or the ratcheted floors stop reproducing across
#      templates.
#
# WHAT THIS DELIBERATELY DOES NOT CHECK (see root task spec):
#   - Runner *implementations* or line counts — different languages, only
#     the surface has to match.
#   - Flag-level or behavioral parity (e.g. whether `--min=abc` errors the
#     same way in all four runners). That needs executing each runner, not
#     statically reading its source — out of scope for this gate.
#
# Run directly: bash scripts/parity-gate.sh   (wired as `make parity`)

set -u -o pipefail
cd "$(dirname "$0")/.." || exit 1

GREEN=$'\033[32m'
RED=$'\033[31m'
RESET=$'\033[0m'

FAILED=0
fail() { printf "  %s✗%s parity: %s\n" "$RED" "$RESET" "$1"; FAILED=1; }
hint() { printf "  ↳ fix: %s\n" "$1"; }

# ── Allowlist: intentional cross-template command-surface divergence ───────
# Format (one per line): <template>:<command>  # <reason>
# Consulted by check 3 (cross-template surface parity) for commands OUTSIDE
# the 20-item core list, and by check 1a for runner-vs-Makefile mismatches.
# Core commands may never be allowlisted here — they are non-negotiable.
ALLOWLIST="
bun:format      # biome's fix already formats + lints in one pass; no separate format step
go:format       # gofmt is folded into \`fix\`; no separate format command
rust:format     # rustfmt is folded into \`fix\`; no separate format command
go:typecheck    # the go compiler (build/vet) is the type checker; no separate typecheck command
rust:typecheck  # rustc is the type checker; no separate typecheck command
go:deadcode     # golangci-lint's \`unused\` linter (run by lint/ci) covers it; x/tools/cmd/deadcode needs a main package, this template is a library
rust:deadcode   # rust's dead_code lint, denied under ci's strict clippy, covers it
python:test-cov # test-cov is a go-only alias for \`coverage\`, not a general concept
bun:test-cov    # test-cov is a go-only alias for \`coverage\`, not a general concept
rust:test-cov   # test-cov is a go-only alias for \`coverage\`, not a general concept
"

is_allowlisted() { # $1=template $2=command
  printf '%s\n' "$ALLOWLIST" | grep -qE "^${1}:${2}([[:space:]#]|\$)"
}

# The 20 command names the behavior contract requires of every language
# template (root CLAUDE.md "Commands (inside a template)" table + the
# per-template Definition-of-done bullets). Never allowlist-exempt.
# gherkin-guard belongs here for the same reason arch-config-guard does: both
# are Layer-2 rules with mechanical enforcement, so a template that silently
# dropped one would lose contract coverage, not just a convenience command.
CORE_COMMANDS="check pre-commit pre-push ci audit post-edit stop-hook complexity crap acceptance coverage mutation arch arch-config-guard gherkin-guard suppressions agents-md-drift sync-agents-md setup-hooks clean"

is_core() { # $1=command
  case " $CORE_COMMANDS " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

TEMPLATES="python bun go rust"

# ── Extraction ───────────────────────────────────────────────────────────
runner_tasks() { # $1=template -> sorted unique command names, one per line
  case "$1" in
    python)
      awk '/^TASKS: dict/,/^}/' python/harness.py \
        | grep -oE '^[[:space:]]*"[a-zA-Z0-9_-]+":' \
        | sed -E 's/^[[:space:]]*"//; s/":$//' ;;
    bun)
      awk '/^const TASKS/,/^};/' bun/harness.ts \
        | grep -oE "^[[:space:]]*'?[a-zA-Z0-9_-]+'?:" \
        | sed -E "s/^[[:space:]]*//; s/'//g; s/:\$//" ;;
    go)
      awk '/^var tasks = \[\]task\{/,/^\}/' go/harness.go \
        | grep -oE '\{"[a-zA-Z0-9_-]+"' \
        | sed -E 's/^\{"//; s/"$//' ;;
    rust)
      awk '/^const COMMANDS/,/^\];/' rust/harness.rs \
        | grep -oE '\("[a-zA-Z0-9_-]+"' \
        | sed -E 's/^\("//; s/"$//' ;;
  esac | sed '/^$/d' | sort -u
}

runner_source() { # $1=template -> path, for messages
  case "$1" in
    python) echo "python/harness.py (TASKS)" ;;
    bun) echo "bun/harness.ts (TASKS)" ;;
    go) echo "go/harness.go (tasks)" ;;
    rust) echo "rust/harness.rs (COMMANDS)" ;;
  esac
}

makefile_tasks() { # $1=template -> sorted unique HARNESS_TARGETS entries
  awk '
    /^HARNESS_TARGETS[ \t]*:=/ { f=1 }
    f {
      line = $0
      gsub(/\\[ \t]*$/, "", line)
      print line
      if ($0 !~ /\\[ \t]*$/) exit
    }
  ' "$1/Makefile" 2>/dev/null \
    | sed 's/HARNESS_TARGETS[[:space:]]*:=//' \
    | tr -s ' \t' '\n' | sed '/^$/d' | sort -u
}

bun_scripts() { # -> sorted unique bun/package.json "scripts" keys
  awk '/"scripts"[[:space:]]*:[[:space:]]*\{/{f=1;next} f && /^[[:space:]]*\}/{exit} f' bun/package.json \
    | grep -oE '"[a-zA-Z0-9_-]+":' | sed 's/"//g; s/://' | sort -u
}

claude_invocations() { # $1=file $2=prefix -> commands invoked as "$2 <cmd>"
  grep -oE "${2} [a-zA-Z0-9_-]+" "$1" 2>/dev/null | awk '{print $NF}' | sort -u
}

lizard_pin_file() { # $1=template -> the file that pins lizard for it
  case "$1" in
    python) echo "python/pyproject.toml" ;;
    bun) echo "bun/harness.ts" ;;
    go) echo "go/harness.go" ;;
    rust) echo "rust/harness.rs" ;;
  esac
}

lizard_pins() { # $1=template -> distinct pinned lizard versions, one per line
  # Matches the `uvx lizard@<ver>` literal (bun/go/rust runners) and the
  # `lizard==<ver>` dev-dependency spec (python/pyproject.toml).
  grep -oE 'lizard(@|==)[0-9][0-9A-Za-z.+-]*' "$(lizard_pin_file "$1")" 2>/dev/null \
    | sed -E 's/^lizard(@|==)//' | sort -u
}

set_diff() { # $1=newline-list $2=newline-list -> lines in $1 not in $2
  comm -23 <(printf '%s\n' "$1" | sed '/^$/d' | sort -u) \
           <(printf '%s\n' "$2" | sed '/^$/d' | sort -u)
}

contains_line() { # $1=newline-separated set $2=item -> membership test
  printf '%s\n' "$1" | grep -qxF "$2"
}

# ── Per-template extraction, with a guard against a stale extraction ──────
# pattern (e.g. another agent restructuring TASKS/tasks/COMMANDS mid-edit).
EXTRACTION_OK=1
RUNNER_python="" RUNNER_bun="" RUNNER_go="" RUNNER_rust=""
MAKE_python="" MAKE_bun="" MAKE_go="" MAKE_rust=""

for t in $TEMPLATES; do
  runner_set=$(runner_tasks "$t")
  make_set=$(makefile_tasks "$t")
  if [ -z "$runner_set" ]; then
    fail "could not extract a command list from $(runner_source "$t") — its dispatch-table pattern may have changed (re-run once the template's edits settle)"
    hint "if this is a real restructure, update runner_tasks() in scripts/parity-gate.sh to match"
    EXTRACTION_OK=0
  fi
  if [ -z "$make_set" ]; then
    fail "could not extract HARNESS_TARGETS from $t/Makefile"
    EXTRACTION_OK=0
  fi
  case "$t" in
    python) RUNNER_python="$runner_set"; MAKE_python="$make_set" ;;
    bun) RUNNER_bun="$runner_set"; MAKE_bun="$make_set" ;;
    go) RUNNER_go="$runner_set"; MAKE_go="$make_set" ;;
    rust) RUNNER_rust="$runner_set"; MAKE_rust="$make_set" ;;
  esac
done

get_runner_set() { case "$1" in python) printf '%s' "$RUNNER_python" ;; bun) printf '%s' "$RUNNER_bun" ;; go) printf '%s' "$RUNNER_go" ;; rust) printf '%s' "$RUNNER_rust" ;; esac; }
get_make_set()   { case "$1" in python) printf '%s' "$MAKE_python" ;; bun) printf '%s' "$MAKE_bun" ;; go) printf '%s' "$MAKE_go" ;; rust) printf '%s' "$MAKE_rust" ;; esac; }

# ── Check 1a: runner <-> Makefile HARNESS_TARGETS, both directions ────────
if [ "$EXTRACTION_OK" = 1 ]; then
  for t in $TEMPLATES; do
    rset=$(get_runner_set "$t"); mset=$(get_make_set "$t")
    for cmd in $(set_diff "$rset" "$mset"); do
      if ! is_allowlisted "$t" "$cmd"; then
        fail "$t/harness dispatches '$cmd' but $t/Makefile HARNESS_TARGETS does not list it"
        hint "add '$cmd' to $t/Makefile HARNESS_TARGETS, or allowlist '$t:$cmd' in scripts/parity-gate.sh with a reason"
      fi
    done
    for cmd in $(set_diff "$mset" "$rset"); do
      fail "$t/Makefile HARNESS_TARGETS lists '$cmd' but $(runner_source "$t") does not dispatch it — \`make $cmd\` would fail"
      hint "remove '$cmd' from $t/Makefile HARNESS_TARGETS, or add it to $t's runner dispatch table"
    done
  done
fi

# ── Check 1b: CLAUDE.md-documented invocations exist in the runner ────────
check_claude_invocations() { # $1=template $2=claude.md path, $3...=prefixes
  local t="$2" file
  t="$1"; file="$2"; shift 2
  local rset; rset=$(get_runner_set "$t")
  for prefix in "$@"; do
    for cmd in $(claude_invocations "$file" "$prefix"); do
      if ! contains_line "$rset" "$cmd"; then
        fail "$t/CLAUDE.md documents \`$prefix $cmd\` but '$cmd' is not in $(runner_source "$t")"
        hint "fix the command name in $t/CLAUDE.md, or add '$cmd' to $t's runner dispatch table"
      fi
    done
  done
}

if [ "$EXTRACTION_OK" = 1 ]; then
  check_claude_invocations python python/CLAUDE.md "uv run harness"
  check_claude_invocations bun bun/CLAUDE.md "bun run" "bun harness\.ts"
  check_claude_invocations go go/CLAUDE.md "go run harness\.go"
  check_claude_invocations rust rust/CLAUDE.md "cargo harness"
fi

# ── Check 1c: bun/CLAUDE.md `bun run <cmd>` <-> bun/package.json scripts ──
# This is the exact shape of the audited bug: a documented `bun run audit`
# with no matching script silently falls through to the OS's own `audit`.
BUN_SCRIPTS=$(bun_scripts)
for cmd in $(claude_invocations bun/CLAUDE.md "bun run"); do
  if ! contains_line "$BUN_SCRIPTS" "$cmd"; then
    fail "bun/CLAUDE.md documents \`bun run $cmd\` but bun/package.json has no \"$cmd\" script — it would fall through to a same-named OS binary"
    hint "add a \"$cmd\" script to bun/package.json (forwarding to \`bun harness.ts $cmd\`), or fix bun/CLAUDE.md"
  fi
done

# ── Check 2: core command coverage (non-negotiable, no allowlist) ─────────
if [ "$EXTRACTION_OK" = 1 ]; then
  for cmd in $CORE_COMMANDS; do
    for t in $TEMPLATES; do
      rset=$(get_runner_set "$t")
      if ! contains_line "$rset" "$cmd"; then
        fail "$t is missing core command '$cmd' (required in python/bun/go/rust — see $(runner_source "$t"))"
        hint "add '$cmd' to $t's runner dispatch table and $t/Makefile HARNESS_TARGETS"
      fi
    done
  done
fi

# ── Check 3: cross-template surface parity beyond the core 20 ─────────────
UNION_COUNT=0
ALLOWED_COUNT=0
if [ "$EXTRACTION_OK" = 1 ]; then
  UNION=$(printf '%s\n%s\n%s\n%s\n' "$RUNNER_python" "$RUNNER_bun" "$RUNNER_go" "$RUNNER_rust" | sed '/^$/d' | sort -u)
  UNION_COUNT=$(printf '%s\n' "$UNION" | grep -c .)
  for cmd in $UNION; do
    is_core "$cmd" && continue
    for t in $TEMPLATES; do
      rset=$(get_runner_set "$t")
      contains_line "$rset" "$cmd" && continue
      if is_allowlisted "$t" "$cmd"; then
        ALLOWED_COUNT=$((ALLOWED_COUNT + 1))
        continue
      fi
      others=$(for o in $TEMPLATES; do
        [ "$o" = "$t" ] && continue
        oset=$(get_runner_set "$o")
        contains_line "$oset" "$cmd" && echo "$o"
      done | tr '\n' ' ')
      fail "$t is missing '$cmd' (present in: ${others% })"
      hint "add '$cmd' to $t, or allowlist '$t:$cmd' in scripts/parity-gate.sh with a reason"
    done
  done
fi

# ── Check 4: the lizard pin is identical across all four templates ────────
# Every template's complexity, CRAP, and duplication gates shell out to the
# same `uvx lizard@<ver>`; a floor in one template's .harness-baseline only
# reproduces in another if they run the same lizard release.
LIZARD_PIN=""
LIZARD_REPORT=""
for t in $TEMPLATES; do
  pins=$(lizard_pins "$t")
  pin_count=$(printf '%s\n' "$pins" | grep -c .)
  if [ "$pin_count" -eq 0 ]; then
    fail "could not extract a lizard pin from $(lizard_pin_file "$t") — expected a \`lizard@<ver>\` (runner) or \`lizard==<ver>\` (pyproject) literal"
    hint "pin lizard in $(lizard_pin_file "$t"), or update lizard_pins() in scripts/parity-gate.sh if the pin moved"
    continue
  fi
  if [ "$pin_count" -gt 1 ]; then
    fail "$(lizard_pin_file "$t") pins lizard to more than one version: $(printf '%s' "$pins" | tr '\n' ' ')"
    hint "use a single lizard version everywhere in $(lizard_pin_file "$t")"
    continue
  fi
  LIZARD_REPORT="${LIZARD_REPORT}${t}=${pins} "
  LIZARD_PIN="${LIZARD_PIN}${pins}
"
done
LIZARD_DISTINCT=$(printf '%s' "$LIZARD_PIN" | sed '/^$/d' | sort -u | grep -c .)
if [ "$LIZARD_DISTINCT" -gt 1 ]; then
  fail "lizard pin differs across templates: ${LIZARD_REPORT% }"
  hint "pin the same lizard version in bun/harness.ts, go/harness.go, rust/harness.rs (lizard@<ver>) and python/pyproject.toml (lizard==<ver>)"
fi

# ── Summary ─────────────────────────────────────────────────────────────
if [ "$FAILED" -eq 0 ]; then
  printf "  %s✓%s parity (python bun go rust · %d core commands · %d total · %d allowlisted exceptions · lizard@%s)\n" \
    "$GREEN" "$RESET" "$(printf '%s\n' $CORE_COMMANDS | wc -w | tr -d ' ')" "$UNION_COUNT" "$ALLOWED_COUNT" \
    "$(printf '%s' "$LIZARD_PIN" | sed '/^$/d' | sort -u)"
  exit 0
fi
exit 1
