#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
runner_source="$repo_root/rust/harness.rs"

managed_rustc=${HARNESS_TEST_RUSTC:-}
if [ -z "$managed_rustc" ]; then
  for candidate in \
    /private/tmp/harness-rust-real-home/tools/rust/1.97.1/rustup/toolchains/1.97.1-*/bin/rustc \
    "$HOME"/.local/share/harness/tools/rust/1.97.1/rustup/toolchains/1.97.1-*/bin/rustc
  do
    if [ -x "$candidate" ]; then
      managed_rustc=$candidate
      break
    fi
  done
fi
if [ -z "$managed_rustc" ] || [ ! -x "$managed_rustc" ]; then
  echo "managed Rust 1.97.1 not found; set HARNESS_TEST_RUSTC" >&2
  exit 1
fi
case "$($managed_rustc --version)" in
  "rustc 1.97.1 "*) ;;
  *)
    echo "expected managed Rust 1.97.1 at $managed_rustc" >&2
    exit 1
    ;;
esac

tmp=$(mktemp -d "${TMPDIR:-/tmp}/harness-rust-runner.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

fixture="$tmp/repo"
fake_bin="$tmp/bin"
command_log="$tmp/commands.log"
poison_log="$tmp/poison.log"
workspace_log="$tmp/workspace.log"
mkdir -p "$fixture/.harness" "$fixture/src" "$fixture/tests" \
  "$fixture/target/llvm-cov" "$fake_bin"
fixture_real=$(CDPATH= cd -- "$fixture" && pwd -P)
: >"$command_log"
: >"$poison_log"
: >"$workspace_log"

"$managed_rustc" --edition=2024 "$runner_source" -o "$tmp/harness-rust"

cat >"$fake_bin/lizard" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf '[lizard]'
  for arg in "$@"; do
    printf '[%s]' "$arg"
  done
  printf '\n'
} >>"$HARNESS_CMD_LOG"

case " $* " in
  *" --csv "*)
    printf '%s\n' '1,1,1,0,1,"foo@1-1@src/lib.rs",src/lib.rs,foo,"fn foo()",1,1'
    ;;
esac
EOF

cat >"$fake_bin/cargo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf '[cargo][LLVM_COV=%s][LLVM_PROFDATA=%s]' "${LLVM_COV-unset}" "${LLVM_PROFDATA-unset}"
  for arg in "$@"; do
    printf '[%s]' "$arg"
  done
  printf '\n'
} >>"$HARNESS_CMD_LOG"

if [ "${1:-}" = mutants ]; then
  echo "ambient cargo-mutants was invoked" >&2
  exit 91
fi
if [ "${1:-}" = llvm-cov ] && [ "${2:-}" != --version ]; then
  if [ "${LLVM_COV+x}" = x ] || [ "${LLVM_PROFDATA+x}" = x ]; then
    echo "cargo-llvm-cov inherited an ambient LLVM override" >&2
    exit 92
  fi
fi
EOF

cat >"$fake_bin/uvx" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '[uvx]' >>"$HARNESS_POISON_LOG"
for arg in "$@"; do
  printf '[%s]' "$arg" >>"$HARNESS_POISON_LOG"
done
printf '\n' >>"$HARNESS_POISON_LOG"
echo "ambient uvx was invoked" >&2
exit 93
EOF

cat >"$fake_bin/cargo-mutants" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '[cargo-mutants]' >>"$HARNESS_POISON_LOG"
echo "ambient cargo-mutants was invoked" >&2
exit 94
EOF

chmod +x "$fake_bin/lizard" "$fake_bin/cargo" "$fake_bin/uvx" "$fake_bin/cargo-mutants"

cat >"$fixture/.harness/workspace.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf '[OFFLINE=%s]' "${OFFLINE:-}"
  for arg in "$@"; do
    printf '[%s]' "$arg"
  done
  printf '\n'
} >>"$HARNESS_WORKSPACE_LOG"
EOF
chmod +x "$fixture/.harness/workspace.sh"

cat >"$fixture/src/lib.rs" <<'EOF'
pub fn foo() {}
EOF
: >"$fixture/tests/smoke.rs"
cat >"$fixture/target/llvm-cov/lcov.info" <<'EOF'
TN:
SF:src/lib.rs
DA:1,1
end_of_record
EOF

run_task() {
  (
    cd "$fixture"
    HARNESS_CMD_LOG="$command_log" \
      HARNESS_POISON_LOG="$poison_log" \
      HARNESS_WORKSPACE_LOG="$workspace_log" \
      LLVM_COV=/poison/system/llvm-cov \
      LLVM_PROFDATA=/poison/system/llvm-profdata \
      PATH="$fake_bin:/usr/bin:/bin" \
      "$tmp/harness-rust" "$@"
  )
}

: >"$command_log"
run_task complexity
cat >"$tmp/expected-complexity.log" <<'EOF'
[lizard][-l][rust][src][tests][-C][15][-a][8][-L][100][-i][0]
EOF
diff -u "$tmp/expected-complexity.log" "$command_log"

: >"$command_log"
touch -t 202001010000 "$fixture/target/llvm-cov/lcov.info"
touch -t 202001010001 "$fixture/src/lib.rs"
run_task crap
{
  printf '%s\n' \
    '[cargo][LLVM_COV=unset][LLVM_PROFDATA=unset][llvm-cov][--version]' \
    '[cargo][LLVM_COV=unset][LLVM_PROFDATA=unset][llvm-cov][--no-report]'
  printf '%s%s%s\n' \
    '[cargo][LLVM_COV=unset][LLVM_PROFDATA=unset][llvm-cov][report][--lcov][--output-path][' \
    "$fixture_real/target/llvm-cov/lcov.info" \
    ']'
  printf '%s\n' '[lizard][-l][rust][src][--csv]'
} >"$tmp/expected-crap.log"
diff -u "$tmp/expected-crap.log" "$command_log"

: >"$command_log"
run_task coverage
cat >"$tmp/expected-coverage-prefix.log" <<'EOF'
[cargo][LLVM_COV=unset][LLVM_PROFDATA=unset][llvm-cov][--version]
[cargo][LLVM_COV=unset][LLVM_PROFDATA=unset][llvm-cov][--no-report]
EOF
sed -n '1,2p' "$command_log" >"$tmp/actual-coverage-prefix.log"
diff -u "$tmp/expected-coverage-prefix.log" "$tmp/actual-coverage-prefix.log"
if [ "$(grep -c '^\[cargo\]\[LLVM_COV=unset\]\[LLVM_PROFDATA=unset\]\[llvm-cov\]' "$command_log")" -ne 4 ]; then
  echo "expected the cargo-llvm-cov probe and all three executions to remove ambient LLVM overrides" >&2
  exit 1
fi

: >"$command_log"
run_task mutation >"$tmp/mutation.out"
if [ -s "$command_log" ] || [ -s "$poison_log" ]; then
  echo "mutation probed or ran an ambient tool" >&2
  exit 1
fi
grep -F "Mutation skipped (cargo-mutants is not provisioned)" "$tmp/mutation.out" >/dev/null

: >"$workspace_log"
OFFLINE=1 run_task setup-hooks
cat >"$tmp/expected-workspace.log" <<'EOF'
[OFFLINE=1][install-hooks]
EOF
diff -u "$tmp/expected-workspace.log" "$workspace_log"
if [ -s "$command_log" ] || [ -s "$poison_log" ]; then
  echo "setup-hooks invoked a tool outside the workspace provisioner" >&2
  exit 1
fi

if grep -Eq '"uvx"|lizard@1\.22\.2|/opt/homebrew/opt/llvm/bin|/usr/local/opt/llvm/bin|"/usr/bin"|tool_installed\("mutants"\)|\["cargo", "mutants"' "$runner_source"; then
  echo "Rust runner still contains an ambient managed-tool path" >&2
  exit 1
fi

echo "rust runner managed tool vectors: ok"
