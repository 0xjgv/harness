#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
tools_root=${HARNESS_WORKSPACE_TOOLS:-$HOME/.local/share/harness/tools}
managed_python=$tools_root/python/3.13.15/runtime/bin/python3.13

if [ -x "$managed_python" ]; then
  python=$managed_python
elif command -v python3 >/dev/null 2>&1; then
  python=$(command -v python3)
else
  printf '%s\n' 'template Stop wiring test requires Python 3' >&2
  exit 1
fi

passed=0

check_stop() {
  relative_path=$1
  expected_command=$2
  expected_timeout=$3
  expected_status=$4
  hook_kind=$5
  "$python" - \
    "$repo_root/$relative_path" \
    "$expected_command" \
    "$expected_timeout" \
    "$expected_status" \
    "$hook_kind" <<'PY'
import json
import sys


path = sys.argv[1]
expected = sys.argv[2]
expected_timeout = sys.argv[3]
expected_status = sys.argv[4]
hook_kind = sys.argv[5]

with open(path, encoding="utf-8") as source:
    document = json.load(source)

try:
    stop = document["hooks"]["Stop"]
except (KeyError, TypeError):
    raise SystemExit(f"{path}: hooks.Stop is missing")

if not isinstance(stop, list) or len(stop) != 1:
    raise SystemExit(f"{path}: hooks.Stop must be a one-item list")

stop_entry = stop[0]
if not isinstance(stop_entry, dict):
    raise SystemExit(f"{path}: hooks.Stop[0] must be an object")

nested_hooks = stop_entry.get("hooks")
if not isinstance(nested_hooks, list) or len(nested_hooks) != 1:
    raise SystemExit(f"{path}: hooks.Stop[0].hooks must be a one-item list")

handler = nested_hooks[0]
if not isinstance(handler, dict):
    raise SystemExit(f"{path}: hooks.Stop[0].hooks[0] must be an object")


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


handlers = [
    value
    for value in objects(stop)
    if value.get("type") == "command" or "command" in value
]
if len(handlers) != 1 or handlers[0] is not handler:
    raise SystemExit(
        f"{path}: hooks.Stop[0].hooks must contain exactly one command handler; "
        f"found {len(handlers)}"
    )

if handler.get("type") != "command" or handler.get("command") != expected:
    raise SystemExit(
        f"{path}: Stop handler must contain type=command and the managed command "
        "in the same object"
    )

if expected_timeout != "-":
    if type(handler.get("timeout")) is not int or handler["timeout"] != int(expected_timeout):
        raise SystemExit(f"{path}: {hook_kind} Stop timeout must be {expected_timeout}")
    if handler.get("statusMessage") != expected_status:
        raise SystemExit(
            f"{path}: {hook_kind} Stop statusMessage must be {expected_status!r}"
        )


occurrences = []


def find_command_text(value, parent=None, key=None):
    if isinstance(value, str):
        if expected in value:
            occurrences.append((parent, key, value))
    elif isinstance(value, dict):
        for child_key, child in value.items():
            find_command_text(child, value, child_key)
    elif isinstance(value, list):
        for child in value:
            find_command_text(child, value, None)


find_command_text(document)
if (
    len(occurrences) != 1
    or occurrences[0][0] is not handler
    or occurrences[0][1] != "command"
    or occurrences[0][2] != expected
):
    raise SystemExit(
        f"{path}: managed Stop command text must occur only in its command handler"
    )
PY
  passed=$((passed + 1))
  printf 'ok %d - %s uses the managed %s Stop command\n' \
    "$passed" "$relative_path" "$hook_kind"
}

while IFS='|' read -r relative_path expected_command; do
  [ -n "$relative_path" ] || continue
  check_stop "$relative_path" "$expected_command" - - Claude
done <<'CASES'
python/.claude/settings.json|cd $CLAUDE_PROJECT_DIR && make stop-hook
bun/.claude/settings.json|cd $CLAUDE_PROJECT_DIR && make stop-hook
go/.claude/settings.json|cd $CLAUDE_PROJECT_DIR && make stop-hook
rust/.claude/settings.json|cd $CLAUDE_PROJECT_DIR && make stop-hook
CASES

while IFS='|' read -r relative_path expected_command expected_timeout expected_status; do
  [ -n "$relative_path" ] || continue
  check_stop \
    "$relative_path" \
    "$expected_command" \
    "$expected_timeout" \
    "$expected_status" \
    Codex
done <<'CASES'
bun/.codex/hooks.json|cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook|300|Running stop-hook checks
go/.codex/hooks.json|cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook|300|Running stop-hook checks
python/.codex/hooks.json|cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook|300|Running stop-hook checks
rust/.codex/hooks.json|cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook|300|Running stop-hook checks
CASES

tmp=$(mktemp -d "${TMPDIR:-/tmp}/harness-template-stop.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM
runtime_case=0

while IFS='|' read -r json_relative wrapper_relative label; do
  [ -n "$json_relative" ] || continue
  runtime_case=$((runtime_case + 1))
  case_root=$tmp/$runtime_case
  mkdir -p "$case_root/bin"

  wrapper=$repo_root/$wrapper_relative
  [ -f "$wrapper" ] || {
    printf '%s\n' "missing Codex Stop wrapper: $wrapper" >&2
    exit 1
  }
  [ -x "$wrapper" ] || {
    printf '%s\n' "Codex Stop wrapper is not executable: $wrapper" >&2
    exit 1
  }

  cat >"$case_root/bin/git" <<'SH'
#!/bin/sh
set -u

if [ "$#" -eq 2 ] && [ "$1" = rev-parse ] && [ "$2" = --show-toplevel ]; then
  printf '%s\n' "$EXPECTED_GIT_ROOT"
  exit 0
fi
exit 91
SH

  cat >"$case_root/bin/make" <<'SH'
#!/bin/sh
set -u

{
  printf '%s\n' make
  for argument do
    printf '%s\n' "$argument"
  done
} >"$STOP_ARGV_LOG"
SH
  chmod +x "$case_root/bin/git" "$case_root/bin/make"

  codex_command=$("$python" - "$repo_root/$json_relative" <<'PY'
import json
import sys


with open(sys.argv[1], encoding="utf-8") as source:
    document = json.load(source)
print(document["hooks"]["Stop"][0]["hooks"][0]["command"])
PY
  )

  template_relative=${json_relative%/.codex/hooks.json}
  stop_output=$(
    cd "$case_root"
    EXPECTED_GIT_ROOT=$repo_root/$template_relative \
      STOP_ARGV_LOG=$case_root/argv \
      PATH=$case_root/bin:$PATH \
      /bin/bash -c "$codex_command"
  )

  [ "$stop_output" = '{"continue":true}' ] || {
    printf '%s\n' "$label Codex Stop wrapper returned unexpected output: $stop_output" >&2
    exit 1
  }
  printf '%s\n' make stop-hook >"$case_root/expected-argv"
  cmp -s "$case_root/expected-argv" "$case_root/argv" || {
    printf '%s\n' "$label Codex Stop wrapper did not receive make stop-hook as argv" >&2
    exit 1
  }
  passed=$((passed + 1))
  printf 'ok %d - %s Codex Stop command executes the wrapper with make stop-hook\n' \
    "$passed" "$label"
done <<'CASES'
bun/.codex/hooks.json|bun/.codex/hooks/codex-stop-hook.sh|Bun
go/.codex/hooks.json|go/.codex/hooks/codex-stop-hook.sh|Go
python/.codex/hooks.json|python/.codex/hooks/codex-stop-hook.sh|Python
rust/.codex/hooks.json|rust/.codex/hooks/codex-stop-hook.sh|Rust
CASES

printf '1..%d\n' "$passed"
