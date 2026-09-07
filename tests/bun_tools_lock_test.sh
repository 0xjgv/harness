#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
TOOLS=$ROOT/.harness/bun-tools
PACKAGE=$TOOLS/package.json
LOCK=$TOOLS/bun.lock
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-bun-tools-lock.XXXXXX")
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

file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

[ -f "$LOCK" ] || fail "Bun tools lock exists"

grep -Fx '  "lockfileVersion": 1,' "$LOCK" >/dev/null || fail "unexpected lock format"
grep -Fx '  "configVersion": 1,' "$LOCK" >/dev/null || fail "unexpected config format"
ok "Bun text lock format is explicit"

grep -Fx '        "knip": "5.88.1",' "$LOCK" >/dev/null || \
  fail "workspace root does not pin Knip 5.88.1"
grep -F '"knip": ["knip@5.88.1"' "$LOCK" >/dev/null || \
  fail "resolved package does not pin Knip 5.88.1"
ok "workspace and package resolution agree on Knip 5.88.1"

package_count=$(grep -Ec '^    "[^"]+": \[' "$LOCK")
[ "$package_count" -eq 60 ] || fail "expected 60 transitive package records, got $package_count"
ok "the complete Knip resolution contains 60 package records"

integrity_count=$(grep -Ec 'sha512-' "$LOCK")
[ "$integrity_count" -eq "$package_count" ] || \
  fail "expected one integrity for every package ($package_count), got $integrity_count"
ok "every package record carries registry integrity"

if grep -E 'git\+|github:|file:|workspace:|https?://' "$LOCK" >/dev/null; then
  fail "lock contains a VCS, local, workspace, or URL dependency"
fi
ok "the lock contains only registry packages"

bun_bin=${HARNESS_TEST_BUN:-}
if [ -z "$bun_bin" ] && command -v bun >/dev/null 2>&1; then
  bun_bin=$(command -v bun)
fi
if [ -n "$bun_bin" ]; then
  cp "$PACKAGE" "$TMP/package.json"
  cp "$LOCK" "$TMP/bun.lock"
  before=$(file_sha256 "$TMP/bun.lock")
  "$bun_bin" install --cwd "$TMP" --frozen-lockfile --ignore-scripts --lockfile-only --offline \
    --cache-dir "${BUN_INSTALL_CACHE_DIR:-$TMP/cache}" >/dev/null
  after=$(file_sha256 "$TMP/bun.lock")
  [ "$before" = "$after" ] || fail "frozen offline validation rewrote bun.lock"
  ok "pinned Bun accepts the lock frozen and offline without rewriting it"
else
  printf 'ok %d - pinned Bun frozen/offline probe skipped # SKIP\n' "$((passed + 1))"
  passed=$((passed + 1))
fi

printf '1..%d\n' "$passed"
