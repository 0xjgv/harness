#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
TOOLS=$ROOT/.harness/go-tools
MOD=$TOOLS/go.mod
SUM=$TOOLS/go.sum
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-go-tools-sum.XXXXXX")
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

[ -f "$SUM" ] || fail "Go tools checksum file exists"

line_count=$(wc -l <"$SUM" | tr -d ' ')
[ "$line_count" -eq 142 ] || fail "expected 142 checksum lines, got $line_count"
ok "the complete selected module graph has 142 checksums"

awk 'NF != 3 || $3 !~ /^h1:[A-Za-z0-9+\/=]+$/ { exit 1 }' "$SUM" || \
  fail "go.sum contains a malformed checksum record"
ok "every checksum record uses the Go h1 format"

while IFS=$'\t' read -r module version; do
  grep -F "$module $version h1:" "$SUM" >/dev/null || fail "missing archive checksum for $module $version"
  grep -F "$module $version/go.mod h1:" "$SUM" >/dev/null || fail "missing go.mod checksum for $module $version"
done <<'EXPECTED'
github.com/fe3dback/go-arch-lint	v1.15.0
github.com/go-gremlins/gremlins	v0.5.0
golang.org/x/vuln	v1.1.4
EXPECTED
ok "all approved roots have module and metadata checksums"

duplicate_count=$(sort "$SUM" | uniq -d | wc -l | tr -d ' ')
[ "$duplicate_count" -eq 0 ] || fail "go.sum contains duplicate records"
ok "checksum records are unique"

go_bin=${HARNESS_TEST_GO:-}
if [ -z "$go_bin" ] && command -v go >/dev/null 2>&1; then
  go_bin=$(command -v go)
fi
if [ -n "$go_bin" ]; then
  cp "$MOD" "$TMP/go.mod"
  cp "$SUM" "$TMP/go.sum"
  before_mod=$(file_sha256 "$TMP/go.mod")
  before_sum=$(file_sha256 "$TMP/go.sum")
  (
    cd "$TMP"
    env GOWORK=off GOENV=off GOTOOLCHAIN=local GOPROXY=off \
      GOMODCACHE="${HARNESS_TEST_GOMODCACHE:-$TMP/mod-cache}" \
      GOCACHE="${HARNESS_TEST_GOCACHE:-$TMP/build-cache}" \
      "$go_bin" mod download all
    env GOWORK=off GOENV=off GOTOOLCHAIN=local GOPROXY=off \
      GOMODCACHE="${HARNESS_TEST_GOMODCACHE:-$TMP/mod-cache}" \
      GOCACHE="${HARNESS_TEST_GOCACHE:-$TMP/build-cache}" \
      "$go_bin" list -mod=readonly -m all >/dev/null
  )
  [ "$before_mod" = "$(file_sha256 "$TMP/go.mod")" ] || fail "offline validation rewrote go.mod"
  [ "$before_sum" = "$(file_sha256 "$TMP/go.sum")" ] || fail "offline validation rewrote go.sum"
  ok "managed Go accepts the graph readonly and offline without rewriting it"
else
  printf 'ok %d - managed Go readonly/offline probe skipped # SKIP\n' "$((passed + 1))"
  passed=$((passed + 1))
fi

printf '1..%d\n' "$passed"
