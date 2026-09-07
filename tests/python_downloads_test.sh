#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
DOWNLOADS=$ROOT/.harness/python-downloads.json
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-python-downloads.XXXXXX")
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

assert_count() {
  local expected=$1 pattern=$2 name=$3 actual
  actual=$(grep -c "$pattern" "$DOWNLOADS" || true)
  [ "$actual" -eq "$expected" ] || fail "$name (expected $expected, got $actual)"
  ok "$name"
}

[ -f "$DOWNLOADS" ] || fail "downloads manifest exists"

assert_count 4 '^  "cpython-3\.13\.15-' "exactly four CPython targets are declared"
assert_count 4 '"major": 3' "every target pins Python major 3"
assert_count 4 '"minor": 13' "every target pins Python minor 13"
assert_count 4 '"patch": 15' "every target pins Python patch 15"
assert_count 4 '"build": "20260814"' "every target pins the approved build"
assert_count 4 '^    "variant": null' "every target uses the default build variant"

for key in \
  cpython-3.13.15-darwin-aarch64-none \
  cpython-3.13.15-darwin-x86_64-none \
  cpython-3.13.15-linux-aarch64-gnu \
  cpython-3.13.15-linux-x86_64-gnu; do
  grep -F "\"$key\"" "$DOWNLOADS" >/dev/null || fail "missing target $key"
done
ok "all supported platform keys are present"

while IFS=$'\t' read -r key url sha; do
  grep -F "\"$key\"" "$DOWNLOADS" >/dev/null || fail "missing target $key"
  grep -F "\"url\": \"$url\"" "$DOWNLOADS" >/dev/null || fail "wrong URL for $key"
  grep -F "\"sha256\": \"$sha\"" "$DOWNLOADS" >/dev/null || fail "wrong SHA-256 for $key"
done <<'EXPECTED'
cpython-3.13.15-darwin-aarch64-none	https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.13.15%2B20260814-aarch64-apple-darwin-install_only_stripped.tar.gz	6d472fc49a4d95e58214a992c4c92aa73fe2a935837a01a9a36bab0bec6d72f3
cpython-3.13.15-darwin-x86_64-none	https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.13.15%2B20260814-x86_64-apple-darwin-install_only_stripped.tar.gz	bf87354efcd9ae517da606fcda4e3a3f0d73a6f05ca7cba3c6d3c5270074bfc8
cpython-3.13.15-linux-aarch64-gnu	https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.13.15%2B20260814-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz	985efd78c1c6521b379f7c64c2a25e6a1130f07441d1af8be441aa05260886aa
cpython-3.13.15-linux-x86_64-gnu	https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.13.15%2B20260814-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz	aaca2af2ab4d7b68a712660d1334c0cfd5ec13c0312ccd30c29122d8d0342320
EXPECTED
ok "URLs and SHA-256 values match the canonical workspace lock"

if command -v uv >/dev/null 2>&1; then
  UV_CACHE_DIR=$TMP/cache uv python list 3.13.15 \
    --all-platforms --all-arches --only-downloads --output-format json \
    --python-downloads-json-url "$DOWNLOADS" >"$TMP/list.json"
  for key in \
    cpython-3.13.15-macos-aarch64-none \
    cpython-3.13.15-macos-x86_64-none \
    cpython-3.13.15-linux-aarch64-gnu \
    cpython-3.13.15-linux-x86_64-gnu; do
    grep -F "\"key\":\"$key\"" "$TMP/list.json" >/dev/null || fail "uv rejected target $key"
  done
  ok "uv accepts and normalizes the custom download manifest"
else
  printf 'ok %d - uv schema probe skipped (uv is not installed yet) # SKIP\n' "$((passed + 1))"
  passed=$((passed + 1))
fi

printf '1..%d\n' "$passed"
