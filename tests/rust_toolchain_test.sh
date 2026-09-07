#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
TOOLCHAIN=$ROOT/rust/rust-toolchain.toml
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-rust-toolchain.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$TOOLCHAIN" ] || fail "Rust toolchain manifest exists"
ok "Rust toolchain manifest exists"

printf '%s\n' \
  '[toolchain]' \
  'channel = "1.97.1"' \
  'profile = "minimal"' \
  'components = ["clippy", "llvm-tools-preview", "rustfmt"]' \
  >"$TMP/expected"

cmp -s "$TMP/expected" "$TOOLCHAIN" || \
  fail "Rust toolchain manifest does not match the exact approved contract"
ok "Rust version, profile, components, ordering, and schema are exact"

printf '1..%d\n' "$passed"
