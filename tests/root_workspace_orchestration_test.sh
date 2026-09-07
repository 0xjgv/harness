#!/bin/bash
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-root-workspace.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok %d - %s\n' "$((passed + 1))" "$1" >&2
  exit 1
}

assert_log() {
  name=$1
  expected=$2
  actual=$3
  if ! diff -u "$expected" "$actual"; then
    fail "$name"
  fi
  ok "$name"
}

new_fixture() {
  fixture=$1
  mkdir -p "$fixture/.harness" "$fixture/skills/harness/reference"
  cp "$ROOT/Makefile" "$fixture/Makefile"
  printf 'root instructions\n' >"$fixture/CLAUDE.md"
  cp "$fixture/CLAUDE.md" "$fixture/AGENTS.md"

  for relative in \
    SKILL.md \
    reference/behavior-contract.md \
    reference/adoption-checklist.md \
    reference/settings-json.md \
    reference/python.md \
    reference/bun.md \
    reference/go.md \
    reference/rust.md \
    reference/monorepo.md; do
    mkdir -p "$fixture/skills/harness/$(dirname "$relative")"
    printf 'managed skill: %s\n' "$relative" >"$fixture/skills/harness/$relative"
  done

  cat >"$fixture/.harness/workspace.sh" <<'EOF'
#!/bin/bash
set -eu

command_name=${1:-}
[ "$#" -eq 0 ] || shift
trace="provisioner|$command_name|offline=${OFFLINE:-0}"
for argument in "$@"; do
  trace="$trace|$argument"
done
printf '%s\n' "$trace" >>"$HARNESS_LOG"
printf 'TRACE|%s\n' "$trace"

if [ "${HARNESS_FAIL_PHASE:-}" = "$command_name" ]; then
  exit 73
fi

if [ "$command_name" = sync-skills ]; then
  for destination in \
    "$HOME/.claude/skills/harness" \
    "$HOME/.agents/skills/harness"; do
    mkdir -p "$destination"
    cp -R skills/harness/. "$destination/"
  done
fi

if [ "$command_name" = exec ] && [ "${HARNESS_EXEC_CHILD:-0}" = 1 ]; then
  while [ "$#" -gt 0 ] && [ "$1" != -- ]; do
    shift
  done
  [ "$#" -gt 0 ]
  shift
  exec "$@"
fi
EOF
  chmod 0755 "$fixture/.harness/workspace.sh"

  git -C "$fixture" init -q
  git -C "$fixture" config user.email workspace@example.test
  git -C "$fixture" config user.name 'Workspace Test'
}

add_project() {
  fixture=$1
  directory=$2
  marker=$3
  mkdir -p "$fixture/$directory"
  : >"$fixture/$directory/$marker"
  cat >"$fixture/$directory/Makefile" <<'EOF'
.PHONY: deps workspace bootstrap

deps:
	@printf 'deps|%s|offline=%s\n' "$${PWD##*/}" "$(OFFLINE)" >>"$$HARNESS_LOG"

workspace bootstrap:
	@printf 'forbidden|%s|%s\n' "$${PWD##*/}" "$@" >>"$$HARNESS_LOG"
EOF
}

commit_fixture() {
  fixture=$1
  git -C "$fixture" add .
  git -C "$fixture" commit -qm fixture
}

run_make() {
  fixture=$1
  home=$2
  log=$3
  shift 3
  HOME="$home" HARNESS_LOG="$log" make -s -C "$fixture" "$@"
}

fixture=$TMP/full
home=$TMP/full-home
log=$TMP/full.log
output=$TMP/full.output
mkdir -p "$home"
new_fixture "$fixture"
add_project "$fixture" a-rust Cargo.toml
add_project "$fixture" b-multi harness.ts
: >"$fixture/b-multi/harness.py"
add_project "$fixture" m-python harness.py
add_project "$fixture" y-go harness.go
commit_fixture "$fixture"
: >"$log"

if ! run_make "$fixture" "$home" "$log" workspace OFFLINE=1 >"$output" 2>&1; then
  sed -n '1,240p' "$output" >&2
  fail "workspace converges in the isolated fixture"
fi

cat >"$TMP/full.expected" <<'EOF'
provisioner|preflight|offline=1|common|bun|python|go|rust
provisioner|install|offline=1|common|bun|python|go|rust
deps|a-rust|offline=1
deps|b-multi|offline=1
deps|m-python|offline=1
deps|y-go|offline=1
provisioner|sync-skills|offline=1
provisioner|install-hooks|offline=1
provisioner|verify|offline=1|common|bun|python|go|rust
provisioner|verify|offline=1|common|bun|python|go|rust
EOF
assert_log \
  "workspace provisions the deduplicated union once and dispatches lexical locked deps" \
  "$TMP/full.expected" "$log"

first_verify_line=$(grep -nF \
  'TRACE|provisioner|verify|offline=1|common|bun|python|go|rust' "$output" | head -1 | cut -d: -f1)
root_check_line=$(grep -nF 'skills-drift (canonical ==' "$output" | head -1 | cut -d: -f1)
final_verify_line=$(grep -nF \
  'TRACE|provisioner|verify|offline=1|common|bun|python|go|rust' "$output" | tail -1 | cut -d: -f1)
if [ -z "$first_verify_line" ] || [ -z "$root_check_line" ] || [ -z "$final_verify_line" ] || \
  [ "$first_verify_line" -ge "$root_check_line" ] || [ "$root_check_line" -ge "$final_verify_line" ]; then
  sed -n '1,240p' "$output" >&2
  fail "root check runs between initial and final verification"
fi
ok "root check runs between initial and final verification"

if grep -E '^forbidden\|' "$log" >/dev/null; then
  fail "root workspace never invokes a subproject workspace or bootstrap target"
fi
ok "root workspace never invokes a subproject workspace or bootstrap target"

: >"$log"
if ! run_make "$fixture" "$home" "$log" _run \
  CMD=check DIRS='a-rust b-multi m-python y-go' OFFLINE=1 >"$output" 2>&1; then
  sed -n '1,240p' "$output" >&2
  fail "managed root dispatch succeeds"
fi
cat >"$TMP/run.expected" <<'EOF'
provisioner|exec|offline=1|rust|--|cargo|run|--quiet|--locked|--bin|harness|--|check
provisioner|exec|offline=1|bun|--|bun|harness.ts|check
provisioner|exec|offline=1|python|--|uv|run|--frozen|--no-sync|harness|check
provisioner|exec|offline=1|go|--|go|run|-mod=readonly|harness.go|check
EOF
assert_log "root dispatch uses the exact managed runner for every language" \
  "$TMP/run.expected" "$log"

fake_bin=$TMP/fake-bin
stdin_log=$TMP/pre-push.stdin
child_log=$TMP/pre-push-child.log
cwd_log=$TMP/pre-push-child.cwd
mkdir -p "$fake_bin"
cat >"$fake_bin/bun" <<'EOF'
#!/bin/bash
set -eu
{
  printf 'bun'
  for argument in "$@"; do
    printf '|%s' "$argument"
  done
  printf '\n'
} >"$HARNESS_CHILD_LOG"
pwd -P >"$HARNESS_CWD_LOG"
cat >"$HARNESS_STDIN_LOG"
EOF
chmod 0755 "$fake_bin/bun"
: >"$log"
cat >"$TMP/pre-push.expected" <<'EOF'
refs/heads/main aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/heads/main bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
EOF
if ! PATH="$fake_bin:$PATH" HOME="$home" HARNESS_LOG="$log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_LOG="$child_log" HARNESS_CWD_LOG="$cwd_log" \
  HARNESS_STDIN_LOG="$stdin_log" \
  make -s -C "$fixture" _run CMD=pre-push DIRS=b-multi OFFLINE=1 \
  <"$TMP/pre-push.expected" >"$output" 2>&1; then
  sed -n '1,240p' "$output" >&2
  fail "managed pre-push dispatch succeeds"
fi
cat >"$TMP/pre-push-child.expected" <<'EOF'
bun|harness.ts|pre-push
EOF
assert_log "managed pre-push dispatch executes the exact child command" \
  "$TMP/pre-push-child.expected" "$child_log"
assert_log "managed pre-push dispatch preserves stdin for the child" \
  "$TMP/pre-push.expected" "$stdin_log"
expected_child_cwd=$(cd "$fixture/b-multi" && pwd -P)
printf '%s\n' "$expected_child_cwd" >"$TMP/pre-push-child-cwd.expected"
assert_log "managed pre-push dispatch enters the selected project" \
  "$TMP/pre-push-child-cwd.expected" "$cwd_log"

push_fixture=$TMP/public-pre-push
push_home=$TMP/public-pre-push-home
push_log=$TMP/public-pre-push.log
push_children=$TMP/public-pre-push-children
public_fake_bin=$TMP/public-fake-bin
mkdir -p "$push_home" "$push_children" "$public_fake_bin"
new_fixture "$push_fixture"
add_project "$push_fixture" a-rust Cargo.toml
add_project "$push_fixture" b-bun harness.ts
add_project "$push_fixture" c-bun harness.ts
add_project "$push_fixture" m-python harness.py
add_project "$push_fixture" z-go harness.go
commit_fixture "$push_fixture"
: >"$push_log"
if ! run_make "$push_fixture" "$push_home" "$push_log" workspace OFFLINE=1 \
  >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push fixture workspace converges"
fi
: >"$push_log"
: >"$push_fixture/b-bun/.dependency-cruiser.json"
git -C "$push_fixture" add b-bun/.dependency-cruiser.json
git -C "$push_fixture" commit -qm arch-config
push_local_sha=$(git -C "$push_fixture" rev-parse HEAD)
push_remote_sha=$(git -C "$push_fixture" rev-parse HEAD^)
printf 'refs/heads/unchanged %s refs/heads/unchanged %s\n' \
  "$push_remote_sha" "$push_remote_sha" >"$TMP/public-pre-push.expected"
printf 'refs/heads/main %s refs/heads/main %s\n' \
  "$push_local_sha" "$push_remote_sha" >>"$TMP/public-pre-push.expected"
printf 'refs/heads/main %s refs/heads/main %s\n' \
  "$push_local_sha" "$push_remote_sha" >"$TMP/public-pre-push-protected-first.expected"
printf 'refs/heads/unchanged %s refs/heads/unchanged %s\n' \
  "$push_remote_sha" "$push_remote_sha" >>"$TMP/public-pre-push-protected-first.expected"
capture_tmp=$TMP/'capture$path'
mkdir -p "$capture_tmp"

cat >"$public_fake_bin/managed-child" <<'EOF'
#!/bin/bash
set -eu
tool_name=${0##*/}
project_name=${PWD##*/}
{
  printf '%s' "$tool_name"
  for argument in "$@"; do
    printf '|%s' "$argument"
  done
  printf '\n'
} >"$HARNESS_CHILD_DIR/$project_name.argv"
pwd -P >"$HARNESS_CHILD_DIR/$project_name.cwd"
cat >"$HARNESS_CHILD_DIR/$project_name.stdin"
EOF
chmod 0755 "$public_fake_bin/managed-child"
for tool_name in bun uv go cargo; do
  ln -s managed-child "$public_fake_bin/$tool_name"
done

mktemp_fail_bin=$TMP/mktemp-fail-bin
cat_fail_bin=$TMP/cat-fail-bin
mkdir -p "$mktemp_fail_bin" "$cat_fail_bin"
cat >"$mktemp_fail_bin/mktemp" <<'EOF'
#!/bin/sh
exit 71
EOF
cat >"$cat_fail_bin/cat" <<'EOF'
#!/bin/sh
exit 72
EOF
chmod 0755 "$mktemp_fail_bin/mktemp" "$cat_fail_bin/cat"
for failure_name in mktemp cat; do
  case "$failure_name" in
    mktemp) failure_path=$mktemp_fail_bin ;;
    cat) failure_path=$cat_fail_bin ;;
  esac
  : >"$push_log"
  if PATH="$failure_path:$public_fake_bin:$PATH" HOME="$push_home" \
    TMPDIR="$capture_tmp" HARNESS_LOG="$push_log" HARNESS_EXEC_CHILD=1 \
    HARNESS_CHILD_DIR="$push_children" \
    make -s -C "$push_fixture" pre-push OFFLINE=1 \
    <"$TMP/public-pre-push.expected" >"$output" 2>&1; then
    fail "$failure_name capture failure aborts public pre-push"
  fi
  if [ -s "$push_log" ] || grep -E 'agents-md-drift|skills-drift' "$output" >/dev/null; then
    sed -n '1,260p' "$output" >&2
    fail "$failure_name capture failure stops before root and child gates"
  fi
  ok "$failure_name capture failure stops before root and child gates"
done

: >"$push_log"

if PATH="$public_fake_bin:$PATH" HOME="$push_home" HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$push_children" \
  make -s -C "$push_fixture" pre-push OFFLINE=1 \
  <"$TMP/public-pre-push-protected-first.expected" >"$output" 2>&1; then
  fail "public pre-push blocks an unreviewed architecture change"
fi
if ! grep -F 'Arch config changed: b-bun/.dependency-cruiser.json' "$output" >/dev/null; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push reports an unreviewed architecture change"
fi
ok "public pre-push reports and blocks an unreviewed architecture change"
if [ -s "$push_log" ]; then
  fail "blocked public pre-push stops before child gates"
fi
ok "blocked public pre-push stops before child gates"

if ! PATH="$public_fake_bin:$PATH" HOME="$push_home" TMPDIR="$capture_tmp" \
  HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$push_children" HARNESS_ALLOW_ARCH_CONFIG=1 \
  make -s -C "$push_fixture" pre-push OFFLINE=1 \
  <"$TMP/public-pre-push.expected" >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push replays Git stdin"
fi
arch_gate_line=$(grep -nF 'Arch config guard override: b-bun/.dependency-cruiser.json' "$output" | head -1 | cut -d: -f1)
agents_gate_line=$(grep -nF 'agents-md-drift' "$output" | head -1 | cut -d: -f1)
skills_gate_line=$(grep -nF 'skills-drift (canonical ==' "$output" | head -1 | cut -d: -f1)
child_gate_line=$(grep -nF 'TRACE|provisioner|exec' "$output" | head -1 | cut -d: -f1)
if [ -z "$arch_gate_line" ] || [ -z "$agents_gate_line" ] || \
  [ -z "$skills_gate_line" ] || [ -z "$child_gate_line" ] || \
  [ "$arch_gate_line" -ge "$agents_gate_line" ] || \
  [ "$agents_gate_line" -ge "$skills_gate_line" ] || \
  [ "$skills_gate_line" -ge "$child_gate_line" ]; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push preserves the root gate order"
fi
ok "public pre-push preserves the root gate order"
cat >"$TMP/public-pre-push.log.expected" <<'EOF'
provisioner|exec|offline=1|rust|--|cargo|run|--quiet|--locked|--bin|harness|--|pre-push
provisioner|exec|offline=1|bun|--|bun|harness.ts|pre-push
provisioner|exec|offline=1|bun|--|bun|harness.ts|pre-push
provisioner|exec|offline=1|python|--|uv|run|--frozen|--no-sync|harness|pre-push
provisioner|exec|offline=1|go|--|go|run|-mod=readonly|harness.go|pre-push
EOF
assert_log "public pre-push dispatches every template through managed execution" \
  "$TMP/public-pre-push.log.expected" "$push_log"

for project_name in a-rust b-bun c-bun m-python z-go; do
  case "$project_name" in
    a-rust) expected_argv='cargo|run|--quiet|--locked|--bin|harness|--|pre-push' ;;
    b-bun|c-bun) expected_argv='bun|harness.ts|pre-push' ;;
    m-python) expected_argv='uv|run|--frozen|--no-sync|harness|pre-push' ;;
    z-go) expected_argv='go|run|-mod=readonly|harness.go|pre-push' ;;
  esac
  printf '%s\n' "$expected_argv" >"$TMP/$project_name.argv.expected"
  assert_log "public pre-push preserves exact argv for $project_name" \
    "$TMP/$project_name.argv.expected" "$push_children/$project_name.argv"
  assert_log "public pre-push replays exact stdin for $project_name" \
    "$TMP/public-pre-push.expected" "$push_children/$project_name.stdin"
  expected_push_cwd=$(cd "$push_fixture/$project_name" && pwd -P)
  printf '%s\n' "$expected_push_cwd" >"$TMP/$project_name.cwd.expected"
  assert_log "public pre-push enters $project_name" \
    "$TMP/$project_name.cwd.expected" "$push_children/$project_name.cwd"
done

ordinary_children=$TMP/public-pre-push-ordinary-children
mkdir -p "$ordinary_children"
ordinary_remote_sha=$(git -C "$push_fixture" rev-parse HEAD)
printf 'ordinary change\n' >"$push_fixture/b-bun/ordinary.txt"
git -C "$push_fixture" add b-bun/ordinary.txt
git -C "$push_fixture" commit -qm ordinary-change
ordinary_local_sha=$(git -C "$push_fixture" rev-parse HEAD)
printf 'refs/heads/main %s refs/heads/main %s\n' \
  "$ordinary_local_sha" "$ordinary_remote_sha" >"$TMP/public-pre-push-ordinary.expected"
: >"$push_log"
if ! PATH="$public_fake_bin:$PATH" HOME="$push_home" TMPDIR="$capture_tmp" \
  HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$ordinary_children" \
  make -s -C "$push_fixture" pre-push OFFLINE=1 \
  <"$TMP/public-pre-push-ordinary.expected" >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "ordinary public pre-push reaches every template"
fi
assert_log "ordinary public pre-push reaches every template" \
  "$TMP/public-pre-push.log.expected" "$push_log"
for project_name in a-rust b-bun c-bun m-python z-go; do
  assert_log "ordinary public pre-push replays exact stdin for $project_name" \
    "$TMP/public-pre-push-ordinary.expected" "$ordinary_children/$project_name.stdin"
done

tty_fixture=$TMP/tty-pre-push
tty_home=$TMP/tty-pre-push-home
tty_log=$TMP/tty-pre-push.log
tty_fake_bin=$TMP/tty-pre-push-bin
tty_child_log=$TMP/tty-pre-push-child.log
mkdir -p "$tty_home" "$tty_fake_bin"
new_fixture "$tty_fixture"
add_project "$tty_fixture" app harness.ts
commit_fixture "$tty_fixture"
cat >"$tty_fake_bin/bun" <<'EOF'
#!/bin/bash
set -eu
[ -t 0 ] || exit 76
{
  printf 'bun'
  for argument in "$@"; do
    printf '|%s' "$argument"
  done
  printf '|tty\n'
} >"$HARNESS_TTY_CHILD_LOG"
EOF
chmod 0755 "$tty_fake_bin/bun"
: >"$tty_log"
if ! run_make "$tty_fixture" "$tty_home" "$tty_log" workspace OFFLINE=1 \
  >"$output" 2>&1; then
  fail "terminal pre-push fixture workspace converges"
fi
: >"$tty_log"
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is required for the terminal pre-push regression probe"
fi
if ! python3 - "$tty_fixture" "$tty_home" "$tty_log" "$tty_fake_bin" "$tty_child_log" <<'PY'
import os
import pty
import select
import signal
import sys
import time

fixture, home, log, fake_bin, child_log = sys.argv[1:]
pid, master = pty.fork()
if pid == 0:
    environment = os.environ.copy()
    environment.update(
        HOME=home,
        HARNESS_LOG=log,
        HARNESS_EXEC_CHILD="1",
        HARNESS_TTY_CHILD_LOG=child_log,
        PATH=fake_bin + os.pathsep + environment["PATH"],
    )
    os.chdir(fixture)
    os.execvpe("make", ["make", "-s", "pre-push", "OFFLINE=1"], environment)

deadline = time.monotonic() + 2.0
captured = bytearray()
child_status = None
while time.monotonic() < deadline:
    readable, _, _ = select.select([master], [], [], 0.05)
    if readable:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        captured.extend(chunk)
    completed, wait_status = os.waitpid(pid, os.WNOHANG)
    if completed:
        child_status = wait_status
        break

if child_status is None:
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    sys.stderr.buffer.write(captured)
    raise SystemExit("terminal make pre-push blocked waiting for EOF")
if not os.WIFEXITED(child_status) or os.WEXITSTATUS(child_status) != 0:
    sys.stderr.buffer.write(captured)
    raise SystemExit("terminal make pre-push failed")
PY
then
  fail "manual terminal pre-push returns without waiting for EOF"
fi
ok "manual terminal pre-push returns without waiting for EOF"
cat >"$TMP/tty-pre-push-child.expected" <<'EOF'
bun|harness.ts|pre-push|tty
EOF
assert_log "manual terminal pre-push preserves TTY stdin for the child" \
  "$TMP/tty-pre-push-child.expected" "$tty_child_log"
cat >"$TMP/tty-pre-push-log.expected" <<'EOF'
provisioner|exec|offline=1|bun|--|bun|harness.ts|pre-push
EOF
assert_log "manual terminal pre-push uses managed child execution" \
  "$TMP/tty-pre-push-log.expected" "$tty_log"

: >"$log"
if ! run_make "$fixture" "$home" "$log" workspace OFFLINE=1 >"$output" 2>&1; then
  fail "second workspace run succeeds"
fi
cp "$log" "$TMP/workspace-second.log"
: >"$log"
if ! run_make "$fixture" "$home" "$log" bootstrap OFFLINE=1 >"$output" 2>&1; then
  fail "bootstrap alias succeeds"
fi
assert_log "bootstrap is byte-for-byte equivalent to workspace orchestration" \
  "$TMP/workspace-second.log" "$log"

: >"$log"
if ! run_make "$fixture" "$home" "$log" setup-hooks OFFLINE=1 >"$output" 2>&1; then
  fail "setup-hooks delegates to the provisioner"
fi
cat >"$TMP/setup-hooks.expected" <<'EOF'
provisioner|install-hooks|offline=1
EOF
assert_log "setup-hooks delegates only to collision-safe provisioner logic" \
  "$TMP/setup-hooks.expected" "$log"

empty=$TMP/empty
empty_home=$TMP/empty-home
empty_log=$TMP/empty.log
mkdir -p "$empty_home"
new_fixture "$empty"
commit_fixture "$empty"
: >"$empty_log"
if ! run_make "$empty" "$empty_home" "$empty_log" workspace OFFLINE=0 >"$output" 2>&1; then
  sed -n '1,240p' "$output" >&2
  fail "empty workspace converges"
fi
cat >"$TMP/empty.expected" <<'EOF'
provisioner|preflight|offline=0|common
provisioner|install|offline=0|common
provisioner|sync-skills|offline=0
provisioner|install-hooks|offline=0
provisioner|verify|offline=0|common
provisioner|verify|offline=0|common
EOF
assert_log "empty root provisions only the common profile" "$TMP/empty.expected" "$empty_log"

multi=$TMP/multi
multi_home=$TMP/multi-home
multi_log=$TMP/multi.log
mkdir -p "$multi_home"
new_fixture "$multi"
add_project "$multi" app harness.ts
: >"$multi/app/harness.py"
commit_fixture "$multi"
: >"$multi_log"
if ! run_make "$multi" "$multi_home" "$multi_log" workspace OFFLINE=0 >"$output" 2>&1; then
  sed -n '1,240p' "$output" >&2
  fail "multi-marker workspace converges"
fi
cat >"$TMP/multi.expected" <<'EOF'
provisioner|preflight|offline=0|common|bun
provisioner|install|offline=0|common|bun
deps|app|offline=0
provisioner|sync-skills|offline=0
provisioner|install-hooks|offline=0
provisioner|verify|offline=0|common|bun
provisioner|verify|offline=0|common|bun
EOF
assert_log "multi-marker project uses Bun precedence without adding a Python profile" \
  "$TMP/multi.expected" "$multi_log"

failure=$TMP/failure
failure_home=$TMP/failure-home
failure_log=$TMP/failure.log
mkdir -p "$failure_home"
new_fixture "$failure"
add_project "$failure" app harness.py
commit_fixture "$failure"
: >"$failure_log"
if HOME="$failure_home" HARNESS_LOG="$failure_log" HARNESS_FAIL_PHASE=install \
  make -s -C "$failure" workspace OFFLINE=1 >"$output" 2>&1; then
  fail "failed tool installation aborts workspace"
fi
cat >"$TMP/failure.expected" <<'EOF'
provisioner|preflight|offline=1|common|python
provisioner|install|offline=1|common|python
EOF
assert_log "failure stops before dependencies, skills, hooks, checks, and verification" \
  "$TMP/failure.expected" "$failure_log"
if [ -e "$failure_home/.claude" ] || [ -e "$failure_home/.agents" ]; then
  fail "failed installation leaves skill destinations untouched"
fi
ok "failed installation leaves skill destinations untouched"

printf '1..%d\n' "$passed"
