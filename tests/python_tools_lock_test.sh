#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
LOCK=$ROOT/.harness/python-tools.lock

passed=0

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

[ -f "$LOCK" ] || fail "Python tools lock exists"

for requirement in lizard==1.22.2 vulture==2.16 pip-audit==2.10.1; do
  grep -E "^${requirement//./\\.} \\\\$" "$LOCK" >/dev/null || fail "missing exact root $requirement"
done
ok "all approved Python tool roots are exact"

package_count=$(grep -Ec '^[a-z0-9][a-z0-9._-]*==[^ ]+ \\$' "$LOCK")
[ "$package_count" -eq 31 ] || fail "expected 31 resolved packages, got $package_count"
ok "the full resolution contains 31 exact packages"

awk '
  /^[a-z0-9][a-z0-9._-]*==[^ ]+ \\$/ {
    if (package != "" && hashes == 0) exit 1
    package = $0
    hashes = 0
    next
  }
  /^    --hash=sha256:[0-9a-f]{64}( \\)?$/ {
    hashes += 1
    next
  }
  /^#/ || /^$/ { next }
  { exit 1 }
  END { if (package == "" || hashes == 0) exit 1 }
' "$LOCK" || fail "every package must have only SHA-256 distribution hashes"
ok "every resolved package is hash locked"

if grep -E '(^|[[:space:]])(-e|--editable|https?://|file:|\.\./|/Users/)' "$LOCK" >/dev/null; then
  fail "lock contains an editable, URL, or local-path requirement"
fi
ok "the lock contains no editable, URL, or local-path requirements"

for expected in \
  9a640e35103fbad7aeb6bb7ba5a58419054c2f4d347453a93728ccc3ac14bde1 \
  6e0f1c312cef1c87856957e5c2ca9608834a7c794c2180477f30bf0e4cc58eee \
  99ef3f600a317c1945f1e89e227ef26e1c2d618429b8bd3fa6f4f7c440c4611a; do
  grep -F -- "--hash=sha256:$expected" "$LOCK" >/dev/null || fail "missing approved root artifact hash $expected"
done
ok "approved root wheel hashes are retained"

grep -F '# Generated with uv 0.12.5 for CPython 3.13' "$LOCK" >/dev/null || \
  fail "missing generator provenance"
ok "generator and interpreter provenance are recorded"

printf '1..%d\n' "$passed"
