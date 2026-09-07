#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
LOCK=$ROOT/.harness/cargo-modules.lock

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$LOCK" ] || fail "cargo-modules lock exists"

grep -Fx 'version = 4' "$LOCK" >/dev/null || fail "unexpected Cargo.lock format"
ok "Cargo lock format is exact"

package_count=$(grep -c '^\[\[package\]\]$' "$LOCK")
[ "$package_count" -eq 308 ] || fail "expected 308 packages, got $package_count"
ok "the complete selected graph has 308 packages"

awk '
  /^\[\[package\]\]$/ {
    if (seen && source && !checksum) exit 1
    seen=1; name=""; version=""; source=0; checksum=0
    next
  }
  /^name = / { name=$0 }
  /^version = / { version=$0 }
  /^source = / {
    if ($0 != "source = \"registry+https://github.com/rust-lang/crates.io-index\"") exit 1
    source=1
  }
  /^checksum = / {
    value=$0
    sub(/^checksum = \"/, "", value)
    sub(/\"$/, "", value)
    if (length(value) != 64 || value ~ /[^0-9a-f]/) exit 1
    checksum=1
  }
  END {
    if (seen && source && !checksum) exit 1
  }
' "$LOCK" || fail "a registry package lacks its exact SHA-256 checksum"
ok "every registry package has an exact crates.io checksum"

registry_count=$(grep -c '^source = "registry+' "$LOCK")
checksum_count=$(grep -c '^checksum = "[0-9a-f]\{64\}"$' "$LOCK")
[ "$registry_count" -eq 307 ] || fail "expected 307 registry packages, got $registry_count"
[ "$checksum_count" -eq "$registry_count" ] || fail "registry and checksum counts differ"
ok "only the local root package is checksum-free"

awk '
  /^\[\[package\]\]$/ { in_root=0; name=""; version=""; next }
  /^name = "cargo-modules"$/ { name=$0; next }
  name && /^version = "0[.]26[.]0"$/ { version=$0; in_root=1; found=1; next }
  in_root && /^source = / { root_source=1 }
  END { exit !(found && !root_source) }
' "$LOCK" || fail "cargo-modules root identity is not exact or is registry-backed"
ok "cargo-modules 0.26.0 is the exact local root"

if grep -Eq '^source = "(git\+|path\+)' "$LOCK"; then
  fail "lock contains a mutable Git or path dependency"
fi
ok "the graph contains no Git or external path dependency"

crate=${HARNESS_TEST_CARGO_MODULES_CRATE:-}
if [ -n "$crate" ]; then
  [ "$(shasum -a 256 "$crate" | awk '{print $1}')" = \
    'ee1ab050e427d44d4c46cae625888bdf54f7d5f0bf29717569a538448b6f294b' ] || \
    fail "authenticated crate digest differs from the approved artifact"
  tar -xOf "$crate" cargo-modules-0.26.0/Cargo.lock | cmp -s - "$LOCK" || \
    fail "checked-in lock differs from the authenticated package lock"
  ok "lock bytes match the authenticated cargo-modules package"
else
  printf 'ok %d - authenticated crate cross-check skipped # SKIP\n' "$((passed + 1))"
  passed=$((passed + 1))
fi

printf '1..%d\n' "$passed"
