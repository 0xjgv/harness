#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
LOCK=$ROOT/.harness/rust-dist.lock
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-rust-dist.XXXXXX")
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

[ -f "$LOCK" ] || fail "Rust distribution lock exists"

grep -Fx $'format\t1' "$LOCK" >/dev/null || fail "unexpected Rust distribution lock format"
grep -Fx $'release\t1.97.1\t2026-07-16' "$LOCK" >/dev/null || fail "release identity is not exact"
ok "release version and date are exact"

grep -Fx $'manifest\thttps://static.rust-lang.org/dist/channel-rust-1.97.1.toml\t03569b1886ceb5c05276b50c8431ab111de944cd6140fe1fa7d821dd8e0f29cf\thttps://static.rust-lang.org/dist/channel-rust-1.97.1.toml.sha256\t3cd57815146650e28e1756549952f655c683a6576a689fbbc7a5ee9d1c6ffab2' "$LOCK" >/dev/null || \
  fail "release manifest or sidecar identity is not exact"
ok "release manifest and SHA sidecar are pinned"

component_count=$(grep -c '^component' "$LOCK")
[ "$component_count" -eq 24 ] || fail "expected 24 component records, got $component_count"
ok "six components are locked for each of four platforms"

for spec in \
  'darwin-arm64 aarch64-apple-darwin' \
  'darwin-x86_64 x86_64-apple-darwin' \
  'linux-arm64 aarch64-unknown-linux-gnu' \
  'linux-x86_64 x86_64-unknown-linux-gnu'; do
  set -- $spec
  platform=$1
  target=$2
  for component in cargo clippy-preview llvm-tools-preview rust-std rustc rustfmt-preview; do
    count=$(awk -F '\t' -v p="$platform" -v c="$component" -v t="$target" \
      '$1 == "component" && $2 == p && $3 == c && $4 == t { count++ } END { print count + 0 }' "$LOCK")
    [ "$count" -eq 1 ] || fail "missing or duplicate $platform/$component"
  done
done
ok "every platform has the exact required component set and target triple"

awk -F '\t' '
  $1 != "component" { next }
  NF != 6 { exit 1 }
  $5 !~ /^https:\/\/static[.]rust-lang[.]org\/dist\/2026-07-16\// { exit 1 }
  $5 !~ /[.]tar[.]xz$/ { exit 1 }
  length($6) != 64 || $6 ~ /[^0-9a-f]/ { exit 1 }
' "$LOCK" || fail "component URL or digest is malformed"
ok "all components use dated HTTPS xz artifacts and lowercase SHA-256"

duplicate_count=$(grep '^component' "$LOCK" | cut -f2-4 | sort | uniq -d | wc -l | tr -d ' ')
[ "$duplicate_count" -eq 0 ] || fail "duplicate platform/component/target records"
ok "component identities are unique"

channel=${HARNESS_TEST_RUST_CHANNEL_MANIFEST:-}
if [ -n "$channel" ]; then
  awk '
    BEGIN {
      map["aarch64-apple-darwin"]="darwin-arm64"
      map["x86_64-apple-darwin"]="darwin-x86_64"
      map["aarch64-unknown-linux-gnu"]="linux-arm64"
      map["x86_64-unknown-linux-gnu"]="linux-x86_64"
    }
    /^\[pkg\.(cargo|clippy-preview|llvm-tools-preview|rust-std|rustc|rustfmt-preview)\.target\./ {
      section=$0
      sub(/^\[pkg\./, "", section)
      sub(/\]$/, "", section)
      split(section, parts, ".target.")
      component=parts[1]
      target=parts[2]
      platform=map[target]
      wanted=(platform != "")
      next
    }
    wanted && /^xz_url = / {
      url=$0
      sub(/^xz_url = "/, "", url)
      sub(/"$/, "", url)
      next
    }
    wanted && /^xz_hash = / {
      sha=$0
      sub(/^xz_hash = "/, "", sha)
      sub(/"$/, "", sha)
      print "component\t" platform "\t" component "\t" target "\t" url "\t" sha
      wanted=0
    }
  ' "$channel" | sort >"$TMP/upstream"
  grep '^component' "$LOCK" | sort >"$TMP/locked"
  cmp -s "$TMP/upstream" "$TMP/locked" || fail "locked closure differs from the official channel manifest"
  ok "component closure matches the verified official channel manifest"
else
  printf 'ok %d - upstream channel cross-check skipped # SKIP\n' "$((passed + 1))"
  passed=$((passed + 1))
fi

printf '1..%d\n' "$passed"
