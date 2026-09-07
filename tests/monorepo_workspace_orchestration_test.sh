#!/bin/bash
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-monorepo-workspace.XXXXXX")
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
  mkdir -p "$fixture/.harness"
  cp "$ROOT/monorepo/Makefile" "$fixture/Makefile"
  printf 'monorepo instructions\n' >"$fixture/CLAUDE.md"
  cp "$fixture/CLAUDE.md" "$fixture/AGENTS.md"

  cat >"$fixture/.harness/workspace.sh" <<'EOF'
#!/bin/bash
set -eu

command_name=${1:-}
[ "$#" -eq 0 ] || shift
trace="provisioner|$command_name|offline=${OFFLINE:-0}"
for argument in "$@"; do
  trace="$trace|$argument"
done
if [ "${HARNESS_TRACE_PROJECT:-0}" = 1 ]; then
  trace="$trace|project=${PWD##*/}"
fi
printf '%s\n' "$trace" >>"$HARNESS_LOG"
printf 'TRACE|%s\n' "$trace"

if [ "$command_name" = exec ] && [ -n "${HARNESS_PARALLEL_BARRIER_DIR:-}" ]; then
  project_name=${PWD##*/}
  : >"$HARNESS_PARALLEL_BARRIER_DIR/$project_name.ready"
  attempts=0
  while :; do
    ready_count=$(find "$HARNESS_PARALLEL_BARRIER_DIR" -type f -name '*.ready' | wc -l | tr -d ' ')
    [ "$ready_count" -ge "$HARNESS_PARALLEL_BARRIER_COUNT" ] && break
    attempts=$((attempts + 1))
    [ "$attempts" -lt 100 ] || exit 74
    sleep 0.02
  done
fi

if [ "${HARNESS_FAIL_PHASE:-}" = "$command_name" ]; then
  exit 73
fi

case "$command_name" in
  sync-skills)
    mkdir -p "$HOME/.harness-test"
    : >"$HOME/.harness-test/skills-synced"
    ;;
  install-hooks)
    mkdir -p "$HOME/.harness-test"
    : >"$HOME/.harness-test/hooks-installed"
    ;;
esac

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
.PHONY: deps workspace bootstrap setup

deps:
	@printf 'deps|%s|offline=%s\n' "$${PWD##*/}" "$(OFFLINE)" >>"$$HARNESS_LOG"; \
	if [ "$${HARNESS_FAIL_DEPS_DIR:-}" = "$${PWD##*/}" ]; then exit 75; fi

workspace bootstrap setup:
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
add_project "$fixture" c-bun harness.ts
add_project "$fixture" m-python harness.py
add_project "$fixture" y-go harness.go
commit_fixture "$fixture"
: >"$log"

if ! run_make "$fixture" "$home" "$log" workspace OFFLINE=1 >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "workspace converges in the isolated monorepo fixture"
fi

cat >"$TMP/full.expected" <<'EOF'
provisioner|preflight|offline=1|common|bun|python|go|rust
provisioner|install|offline=1|common|bun|python|go|rust
deps|a-rust|offline=1
deps|b-multi|offline=1
deps|c-bun|offline=1
deps|m-python|offline=1
deps|y-go|offline=1
provisioner|sync-skills|offline=1
provisioner|install-hooks|offline=1
provisioner|verify|offline=1|common|bun|python|go|rust
provisioner|exec|offline=1|rust|--|cargo|run|--quiet|--locked|--bin|harness|--|check
provisioner|exec|offline=1|bun|--|bun|harness.ts|check
provisioner|exec|offline=1|bun|--|bun|harness.ts|check
provisioner|exec|offline=1|python|--|uv|run|--frozen|--no-sync|harness|check
provisioner|exec|offline=1|go|--|go|run|-mod=readonly|harness.go|check
provisioner|verify|offline=1|common|bun|python|go|rust
EOF
assert_log \
  "workspace provisions one union, restores lexical deps once, and checks through managed runners" \
  "$TMP/full.expected" "$log"

first_verify_line=$(grep -nF \
  'TRACE|provisioner|verify|offline=1|common|bun|python|go|rust' "$output" | head -1 | cut -d: -f1)
root_drift_line=$(grep -nF 'agents-md-drift (root)' "$output" | head -1 | cut -d: -f1)
arch_guard_line=$(grep -nF 'Arch config guard' "$output" | head -1 | cut -d: -f1)
first_check_line=$(grep -nF 'TRACE|provisioner|exec|offline=1' "$output" | head -1 | cut -d: -f1)
final_verify_line=$(grep -nF \
  'TRACE|provisioner|verify|offline=1|common|bun|python|go|rust' "$output" | tail -1 | cut -d: -f1)
if [ -z "$first_verify_line" ] || [ -z "$root_drift_line" ] || \
  [ -z "$arch_guard_line" ] || [ -z "$first_check_line" ] || [ -z "$final_verify_line" ] || \
  [ "$first_verify_line" -ge "$root_drift_line" ] || \
  [ "$root_drift_line" -ge "$arch_guard_line" ] || \
  [ "$arch_guard_line" -ge "$first_check_line" ] || \
  [ "$first_check_line" -ge "$final_verify_line" ]; then
  sed -n '1,260p' "$output" >&2
  fail "public check gates run between initial and final verification"
fi
ok "public check gates run between initial and final verification"

if grep -E '^forbidden\|' "$log" >/dev/null; then
  fail "monorepo workspace never invokes a child workspace, bootstrap, or setup target"
fi
ok "monorepo workspace never invokes a child workspace, bootstrap, or setup target"

: >"$log"
if ! run_make "$fixture" "$home" "$log" deps \
  OFFLINE=1 PARALLEL=1 JOBS=2 >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "dependency restoration remains serial when parallel checks are requested"
fi
cat >"$TMP/deps.expected" <<'EOF'
deps|a-rust|offline=1
deps|b-multi|offline=1
deps|c-bun|offline=1
deps|m-python|offline=1
deps|y-go|offline=1
EOF
assert_log "PARALLEL never weakens lexical one-pass dependency restoration" \
  "$TMP/deps.expected" "$log"

: >"$log"
parallel_barrier=$TMP/parallel-barrier
mkdir -p "$parallel_barrier"
if ! HOME="$home" HARNESS_LOG="$log" HARNESS_TRACE_PROJECT=1 \
  HARNESS_PARALLEL_BARRIER_DIR="$parallel_barrier" HARNESS_PARALLEL_BARRIER_COUNT=5 \
  make -s -C "$fixture" _run CMD=ci DIRS='a-rust b-multi c-bun m-python y-go' \
  OFFLINE=1 PARALLEL=1 JOBS=5 >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "parallel managed dispatch succeeds"
fi
cat >"$TMP/parallel.expected" <<'EOF'
provisioner|exec|offline=1|rust|--|cargo|run|--quiet|--locked|--bin|harness|--|ci|project=a-rust
provisioner|exec|offline=1|bun|--|bun|harness.ts|ci|project=b-multi
provisioner|exec|offline=1|bun|--|bun|harness.ts|ci|project=c-bun
provisioner|exec|offline=1|python|--|uv|run|--frozen|--no-sync|harness|ci|project=m-python
provisioner|exec|offline=1|go|--|go|run|-mod=readonly|harness.go|ci|project=y-go
EOF
sort "$TMP/parallel.expected" >"$TMP/parallel.expected.sorted"
sort "$log" >"$TMP/parallel.actual.sorted"
assert_log "parallel dispatch keeps every child inside the managed provisioner" \
  "$TMP/parallel.expected.sorted" "$TMP/parallel.actual.sorted"
grep '^TRACE|provisioner|exec' "$output" | sed 's/^TRACE|//' >"$TMP/parallel.output-order"
assert_log "parallel output remains buffered in deterministic project order" \
  "$TMP/parallel.expected" "$TMP/parallel.output-order"

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
  sed -n '1,260p' "$output" >&2
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
mkdir -p "$push_home" "$push_children"
new_fixture "$push_fixture"
add_project "$push_fixture" a-bun harness.ts
add_project "$push_fixture" z-bun harness.ts
commit_fixture "$push_fixture"
: >"$push_fixture/a-bun/.dependency-cruiser.json"
git -C "$push_fixture" add a-bun/.dependency-cruiser.json
git -C "$push_fixture" commit -qm arch-config
push_local_sha=$(git -C "$push_fixture" rev-parse HEAD)
push_remote_sha=$(git -C "$push_fixture" rev-parse HEAD^)
printf 'refs/heads/unchanged %s refs/heads/unchanged %s\n' \
  "$push_remote_sha" "$push_remote_sha" >"$TMP/public-pre-push.expected"
printf 'refs/heads/main %s refs/heads/main %s\n' \
  "$push_local_sha" "$push_remote_sha" >>"$TMP/public-pre-push.expected"
capture_tmp=$TMP/'capture$path'
mkdir -p "$capture_tmp"

cat >"$fake_bin/bun" <<'EOF'
#!/bin/bash
set -eu
project_name=${PWD##*/}
{
  printf 'bun'
  for argument in "$@"; do
    printf '|%s' "$argument"
  done
  printf '\n'
} >"$HARNESS_CHILD_DIR/$project_name.argv"
pwd -P >"$HARNESS_CHILD_DIR/$project_name.cwd"
cat >"$HARNESS_CHILD_DIR/$project_name.stdin"
EOF
chmod 0755 "$fake_bin/bun"

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
  if PATH="$failure_path:$fake_bin:$PATH" HOME="$push_home" \
    TMPDIR="$capture_tmp" HARNESS_LOG="$push_log" HARNESS_EXEC_CHILD=1 \
    HARNESS_CHILD_DIR="$push_children" \
    make -s -C "$push_fixture" pre-push OFFLINE=1 PARALLEL=1 \
    <"$TMP/public-pre-push.expected" >"$output" 2>&1; then
    fail "$failure_name capture failure aborts public pre-push"
  fi
  if [ -s "$push_log" ] || grep -F 'Arch config guard' "$output" >/dev/null; then
    sed -n '1,260p' "$output" >&2
    fail "$failure_name capture failure stops before root and child gates"
  fi
  ok "$failure_name capture failure stops before root and child gates"
done

: >"$push_log"
if PATH="$fake_bin:$PATH" HOME="$push_home" TMPDIR="$capture_tmp" \
  HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$push_children" \
  make -s -C "$push_fixture" pre-push OFFLINE=1 PARALLEL=1 \
  <"$TMP/public-pre-push.expected" >"$output" 2>&1; then
  fail "public pre-push blocks an unreviewed architecture change"
fi
if ! grep -F 'Arch config changed: a-bun/.dependency-cruiser.json' "$output" >/dev/null; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push reports an unreviewed architecture change"
fi
ok "public pre-push reports and blocks an unreviewed architecture change"
if [ -s "$push_log" ]; then
  fail "blocked public pre-push stops before child gates"
fi
ok "blocked public pre-push stops before child gates"

if ! PATH="$fake_bin:$PATH" HOME="$push_home" TMPDIR="$capture_tmp" \
  HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$push_children" HARNESS_ALLOW_ARCH_CONFIG=1 \
  make -s -C "$push_fixture" pre-push OFFLINE=1 PARALLEL=1 \
  <"$TMP/public-pre-push.expected" >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push replays Git stdin"
fi
if ! grep -F 'Arch config guard override: a-bun/.dependency-cruiser.json' "$output" >/dev/null; then
  sed -n '1,260p' "$output" >&2
  fail "public pre-push replays Git stdin to the root architecture guard"
fi
ok "public pre-push replays Git stdin to the root architecture guard"
cat >"$TMP/public-pre-push.log.expected" <<'EOF'
provisioner|exec|offline=1|bun|--|bun|harness.ts|pre-push
provisioner|exec|offline=1|bun|--|bun|harness.ts|pre-push
EOF
sort "$push_log" >"$TMP/public-pre-push.log.sorted"
assert_log "public pre-push dispatches every child through managed execution" \
  "$TMP/public-pre-push.log.expected" "$TMP/public-pre-push.log.sorted"
cat >"$TMP/public-pre-push-child.expected" <<'EOF'
bun|harness.ts|pre-push
EOF
for project_name in a-bun z-bun; do
  assert_log "public pre-push preserves exact argv for $project_name" \
    "$TMP/public-pre-push-child.expected" "$push_children/$project_name.argv"
  assert_log "public pre-push replays exact stdin for $project_name" \
    "$TMP/public-pre-push.expected" "$push_children/$project_name.stdin"
  expected_push_cwd=$(cd "$push_fixture/$project_name" && pwd -P)
  printf '%s\n' "$expected_push_cwd" >"$TMP/$project_name.cwd.expected"
  assert_log "public pre-push enters $project_name" \
    "$TMP/$project_name.cwd.expected" "$push_children/$project_name.cwd"
done

clean_push_children=$TMP/public-pre-push-clean-children
mkdir -p "$clean_push_children"
clean_push_remote_sha=$(git -C "$push_fixture" rev-parse HEAD)
printf 'ordinary change\n' >"$push_fixture/a-bun/ordinary.txt"
git -C "$push_fixture" add a-bun/ordinary.txt
git -C "$push_fixture" commit -qm ordinary-change
clean_push_local_sha=$(git -C "$push_fixture" rev-parse HEAD)
printf 'refs/heads/main %s refs/heads/main %s\n' \
  "$clean_push_local_sha" "$clean_push_remote_sha" >"$TMP/public-pre-push-clean.expected"
: >"$push_log"
if ! PATH="$fake_bin:$PATH" HOME="$push_home" TMPDIR="$capture_tmp" \
  HARNESS_LOG="$push_log" \
  HARNESS_EXEC_CHILD=1 HARNESS_CHILD_DIR="$clean_push_children" \
  make -s -C "$push_fixture" pre-push OFFLINE=1 \
  <"$TMP/public-pre-push-clean.expected" >"$output" 2>&1; then
  sed -n '1,260p' "$output" >&2
  fail "ordinary public pre-push reaches child gates"
fi
if ! grep -F 'Arch config guard' "$output" >/dev/null; then
  sed -n '1,260p' "$output" >&2
  fail "ordinary public pre-push runs the root architecture guard"
fi
ok "ordinary public pre-push runs the root architecture guard"
sort "$push_log" >"$TMP/public-pre-push-clean.log.sorted"
assert_log "ordinary public pre-push reaches every child" \
  "$TMP/public-pre-push.log.expected" "$TMP/public-pre-push-clean.log.sorted"
for project_name in a-bun z-bun; do
  assert_log "ordinary public pre-push replays exact stdin for $project_name" \
    "$TMP/public-pre-push-clean.expected" "$clean_push_children/$project_name.stdin"
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
for alias in bootstrap setup; do
  : >"$log"
  if ! run_make "$fixture" "$home" "$log" "$alias" OFFLINE=1 >"$output" 2>&1; then
    fail "$alias compatibility alias succeeds"
  fi
  assert_log "$alias is byte-for-byte equivalent to workspace orchestration" \
    "$TMP/workspace-second.log" "$log"
done

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
  sed -n '1,260p' "$output" >&2
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
assert_log "empty monorepo provisions only the common profile" \
  "$TMP/empty.expected" "$empty_log"

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
  sed -n '1,260p' "$output" >&2
  fail "multi-marker workspace converges"
fi
cat >"$TMP/multi.expected" <<'EOF'
provisioner|preflight|offline=0|common|bun
provisioner|install|offline=0|common|bun
deps|app|offline=0
provisioner|sync-skills|offline=0
provisioner|install-hooks|offline=0
provisioner|verify|offline=0|common|bun
provisioner|exec|offline=0|bun|--|bun|harness.ts|check
provisioner|verify|offline=0|common|bun
EOF
assert_log "multi-marker project uses Bun precedence without adding a Python profile" \
  "$TMP/multi.expected" "$multi_log"

deps_failure=$TMP/deps-failure
deps_failure_home=$TMP/deps-failure-home
deps_failure_log=$TMP/deps-failure.log
mkdir -p "$deps_failure_home"
new_fixture "$deps_failure"
add_project "$deps_failure" a-bun harness.ts
add_project "$deps_failure" b-python harness.py
add_project "$deps_failure" c-go harness.go
commit_fixture "$deps_failure"
: >"$deps_failure_log"
if HOME="$deps_failure_home" HARNESS_LOG="$deps_failure_log" \
  HARNESS_FAIL_DEPS_DIR=b-python \
  make -s -C "$deps_failure" workspace OFFLINE=1 >"$output" 2>&1; then
  fail "failed child dependency restoration aborts workspace"
fi
cat >"$TMP/deps-failure.expected" <<'EOF'
provisioner|preflight|offline=1|common|bun|python|go
provisioner|install|offline=1|common|bun|python|go
deps|a-bun|offline=1
deps|b-python|offline=1
EOF
assert_log "dependency failure stops before later children, skills, hooks, checks, and verification" \
  "$TMP/deps-failure.expected" "$deps_failure_log"
if [ -e "$deps_failure_home/.harness-test" ]; then
  fail "dependency failure leaves skill and hook destinations untouched"
fi
ok "dependency failure leaves skill and hook destinations untouched"

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
if [ -e "$failure_home/.harness-test" ]; then
  fail "failed installation leaves skill and hook destinations untouched"
fi
ok "failed installation leaves skill and hook destinations untouched"

printf '1..%d\n' "$passed"
