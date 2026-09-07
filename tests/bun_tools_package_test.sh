#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
PACKAGE=$ROOT/.harness/bun-tools/package.json

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$PACKAGE" ] || fail "Bun tools package exists"

grep -Fx '  "private": true,' "$PACKAGE" >/dev/null || fail "tool package is not private"
ok "tool package cannot be published"

grep -Fx '  "packageManager": "bun@1.3.14",' "$PACKAGE" >/dev/null || \
  fail "Bun version is not exact"
ok "package manager matches the approved Bun pin"

grep -Fx '    "knip": "5.88.1"' "$PACKAGE" >/dev/null || fail "Knip version is not exact"
ok "Knip matches the approved pin"

dependency_count=$(grep -Ec '^    "[a-zA-Z0-9@/_-]+": "[^"]+"[,]?$' "$PACKAGE")
[ "$dependency_count" -eq 1 ] || fail "expected one tool dependency, got $dependency_count"
ok "Knip is the only tool dependency"

if grep -E '[~^*]|latest|workspace:|file:|https?://' "$PACKAGE" >/dev/null; then
  fail "package contains a range or mutable dependency source"
fi
ok "the package contains no ranges or mutable sources"

printf '1..%d\n' "$passed"
