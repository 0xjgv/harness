#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
MOD=$ROOT/.harness/go-tools/go.mod

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$MOD" ] || fail "Go tools module exists"

grep -Fx 'module harness.local/workspace-tools' "$MOD" >/dev/null || fail "unexpected module path"
ok "the tools module has a non-publishable local identity"

grep -Fx 'go 1.27.0' "$MOD" >/dev/null || fail "Go toolchain directive is not exact"
ok "the module matches the approved Go pin"

while IFS=$'\t' read -r module version; do
  grep -Fx "$module $version" <(sed 's/^[[:space:]]*//' "$MOD") >/dev/null || \
    fail "missing exact module $module $version"
done <<'EXPECTED'
	github.com/fe3dback/go-arch-lint	v1.15.0
	github.com/go-gremlins/gremlins	v0.5.0
	golang.org/x/vuln	v1.1.4
EXPECTED
ok "all approved Go analyzer modules are exact"

requirement_count=$(grep -Ec '^\t[^ ]+ v[0-9]' "$MOD")
[ "$requirement_count" -eq 3 ] || fail "expected three tool modules, got $requirement_count"
ok "no extra root modules are declared"

if grep -E '@|latest|replace|exclude|retract' "$MOD" >/dev/null; then
  fail "module contains a mutable version or graph override"
fi
ok "the module contains no mutable versions or graph overrides"

printf '1..%d\n' "$passed"
