#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-provisioner-drift.XXXXXX")
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

file_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

printf '%s\n' \
  '.harness/workspace.sh|python/.harness/workspace.sh' \
  '.harness/workspace.sh|bun/.harness/workspace.sh' \
  '.harness/workspace.sh|go/.harness/workspace.sh' \
  '.harness/workspace.sh|rust/.harness/workspace.sh' \
  '.harness/workspace.sh|monorepo/.harness/workspace.sh' \
  '.harness/workspace.lock|python/.harness/workspace.lock' \
  '.harness/workspace.lock|bun/.harness/workspace.lock' \
  '.harness/workspace.lock|go/.harness/workspace.lock' \
  '.harness/workspace.lock|rust/.harness/workspace.lock' \
  '.harness/workspace.lock|monorepo/.harness/workspace.lock' \
  '.harness/python-downloads.json|python/.harness/python-downloads.json' \
  '.harness/python-downloads.json|bun/.harness/python-downloads.json' \
  '.harness/python-downloads.json|go/.harness/python-downloads.json' \
  '.harness/python-downloads.json|rust/.harness/python-downloads.json' \
  '.harness/python-downloads.json|monorepo/.harness/python-downloads.json' \
  '.harness/python-tools.lock|python/.harness/python-tools.lock' \
  '.harness/python-tools.lock|bun/.harness/python-tools.lock' \
  '.harness/python-tools.lock|go/.harness/python-tools.lock' \
  '.harness/python-tools.lock|rust/.harness/python-tools.lock' \
  '.harness/python-tools.lock|monorepo/.harness/python-tools.lock' \
  '.harness/bun-tools/package.json|python/.harness/bun-tools/package.json' \
  '.harness/bun-tools/package.json|bun/.harness/bun-tools/package.json' \
  '.harness/bun-tools/package.json|go/.harness/bun-tools/package.json' \
  '.harness/bun-tools/package.json|rust/.harness/bun-tools/package.json' \
  '.harness/bun-tools/package.json|monorepo/.harness/bun-tools/package.json' \
  '.harness/bun-tools/bun.lock|python/.harness/bun-tools/bun.lock' \
  '.harness/bun-tools/bun.lock|bun/.harness/bun-tools/bun.lock' \
  '.harness/bun-tools/bun.lock|go/.harness/bun-tools/bun.lock' \
  '.harness/bun-tools/bun.lock|rust/.harness/bun-tools/bun.lock' \
  '.harness/bun-tools/bun.lock|monorepo/.harness/bun-tools/bun.lock' \
  '.harness/go-tools/go.mod|python/.harness/go-tools/go.mod' \
  '.harness/go-tools/go.mod|bun/.harness/go-tools/go.mod' \
  '.harness/go-tools/go.mod|go/.harness/go-tools/go.mod' \
  '.harness/go-tools/go.mod|rust/.harness/go-tools/go.mod' \
  '.harness/go-tools/go.mod|monorepo/.harness/go-tools/go.mod' \
  '.harness/go-tools/go.sum|python/.harness/go-tools/go.sum' \
  '.harness/go-tools/go.sum|bun/.harness/go-tools/go.sum' \
  '.harness/go-tools/go.sum|go/.harness/go-tools/go.sum' \
  '.harness/go-tools/go.sum|rust/.harness/go-tools/go.sum' \
  '.harness/go-tools/go.sum|monorepo/.harness/go-tools/go.sum' \
  '.harness/rust-dist.lock|python/.harness/rust-dist.lock' \
  '.harness/rust-dist.lock|bun/.harness/rust-dist.lock' \
  '.harness/rust-dist.lock|go/.harness/rust-dist.lock' \
  '.harness/rust-dist.lock|rust/.harness/rust-dist.lock' \
  '.harness/rust-dist.lock|monorepo/.harness/rust-dist.lock' \
  '.harness/cargo-modules.lock|python/.harness/cargo-modules.lock' \
  '.harness/cargo-modules.lock|bun/.harness/cargo-modules.lock' \
  '.harness/cargo-modules.lock|go/.harness/cargo-modules.lock' \
  '.harness/cargo-modules.lock|rust/.harness/cargo-modules.lock' \
  '.harness/cargo-modules.lock|monorepo/.harness/cargo-modules.lock' \
  >"$TMP/entries"

awk -F '|' '{ print $1 }' "$TMP/entries" \
  | LC_ALL=C sort -u \
  >"$TMP/canonical-sources"

artifact_index=0
while IFS= read -r canonical_relative; do
  [ -n "$canonical_relative" ] || continue
  artifact_index=$((artifact_index + 1))
  artifact_depth=$(printf '%s\n' "$canonical_relative" | awk -F / '{ print NF + 1 }')
  awk -F '|' -v source="$canonical_relative" '$1 == source { print $2 }' \
    "$TMP/entries" \
    | LC_ALL=C sort \
    >"$TMP/$artifact_index.expected-paths"
  find "$ROOT" -mindepth "$artifact_depth" -maxdepth "$artifact_depth" -type f \
    -path "*/$canonical_relative" -print \
    | sed "s#^$ROOT/##" \
    | LC_ALL=C sort \
    >"$TMP/$artifact_index.actual-paths"

  cmp -s \
    "$TMP/$artifact_index.expected-paths" \
    "$TMP/$artifact_index.actual-paths" || {
      printf '%s\n' "$canonical_relative destination path set drifted:" >&2
      diff -u \
        "$TMP/$artifact_index.expected-paths" \
        "$TMP/$artifact_index.actual-paths" >&2 || true
      exit 1
    }
  ok "$canonical_relative destination path set is exact"
done <"$TMP/canonical-sources"

while IFS='|' read -r canonical_relative destination_relative; do
  [ -n "$canonical_relative" ] || continue
  canonical=$ROOT/$canonical_relative
  destination=$ROOT/$destination_relative

  [ -f "$canonical" ] || fail "canonical provisioner artifact is missing: $canonical_relative"
  canonical_mode=$(file_mode "$canonical")

  [ -f "$destination" ] || \
    fail "$destination_relative: provisioner destination is missing"
  cmp -s "$canonical" "$destination" || \
    fail "$destination_relative: bytes differ from $canonical_relative"

  destination_mode=$(file_mode "$destination")
  [ "$destination_mode" = "$canonical_mode" ] || \
    fail "$destination_relative: mode $destination_mode differs from $canonical_relative mode $canonical_mode"

  ok "$destination_relative matches $canonical_relative bytes and mode"
done <"$TMP/entries"

printf '1..%d\n' "$passed"
