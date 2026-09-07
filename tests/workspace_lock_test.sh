#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
SCRIPT="$ROOT/.harness/workspace.sh"
LOCK="$ROOT/.harness/workspace.lock"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-workspace-lock.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

passed=0

ok() {
  printf 'ok %d - %s\n' "$((passed += 1))" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

expect_success() {
  local name=$1
  shift
  if "$@" >"$TMP/output" 2>&1; then
    ok "$name"
  else
    sed 's/^/  /' "$TMP/output" >&2
    fail "$name"
  fi
}

expect_failure() {
  local name=$1 expected=$2
  shift 2
  if "$@" >"$TMP/output" 2>&1; then
    fail "$name (unexpected success)"
  fi
  if ! grep -F "$expected" "$TMP/output" >/dev/null; then
    sed 's/^/  /' "$TMP/output" >&2
    fail "$name (missing: $expected)"
  fi
  ok "$name"
}

copy_lock() {
  local name=$1
  cp "$LOCK" "$TMP/$name.lock"
  printf '%s\n' "$TMP/$name.lock"
}

convert_to_v2() {
  local source=$1 destination=$2
  awk 'BEGIN { FS = OFS = "\t" }
    $1 == "format" { $2 = "2" }
    $1 == "artifact" && NF == 7 { print $1, $2, "-", $3, $4, $5, $6, $7; next }
    { print }
  ' "$source" >"$destination"
}

convert_to_v1() {
  local source=$1 destination=$2
  awk 'BEGIN { FS = OFS = "\t" }
    $1 == "format" { $2 = "1" }
    $1 == "input" { next }
    $1 == "artifact" && NF == 8 { print $1, $2, $4, $5, $6, $7, $8; next }
    { print }
  ' "$source" >"$destination"
}

file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

assert_pin() {
  local profile=$1 name=$2 version=$3 kind=$4
  if ! awk -F '\t' -v p="$profile" -v n="$name" -v v="$version" -v k="$kind" \
    '$1 == "tool" || $1 == "pin" { if ($2 == p && $3 == n && $4 == v && $5 == k) found = 1 } END { exit !found }' \
    "$LOCK"; then
    fail "missing exact pin: $profile/$name@$version ($kind)"
  fi
}

assert_probe() {
  local name=$1 probe=$2 expected=$3
  if ! awk -F '\t' -v n="$name" -v probe="$probe" -v expected="$expected" \
    '$1 == "pin" && $3 == n && $7 == probe && $8 == expected { found++ } END { exit found != 1 }' \
    "$LOCK"; then
    fail "missing exact probe: $name $probe -> $expected"
  fi
}

assert_source() {
  local name=$1 source=$2
  if ! awk -F '\t' -v n="$name" -v source="$source" \
    '$1 == "pin" && $3 == n && $9 == source { found++ } END { exit found != 1 }' "$LOCK"; then
    fail "missing exact source: $name -> $source"
  fi
}

assert_input() {
  local profile=$1 ecosystem=$2 path=$3 expected_sha=$4
  local actual_sha
  actual_sha=$(file_sha256 "$ROOT/$path")
  [ "$actual_sha" = "$expected_sha" ] || fail "unexpected source digest for $path"
  if ! awk -F '\t' -v p="$profile" -v e="$ecosystem" -v path="$path" -v sha="$expected_sha" \
    '$1 == "input" && $2 == p && $3 == e && $4 == path && $5 == sha { found++ } END { exit found != 1 }' \
    "$LOCK"; then
    fail "missing exact ecosystem input: $profile/$ecosystem/$path"
  fi
}

expect_success "production manifest validates" "$SCRIPT" validate-lock
grep -Fx $'format\t2' "$LOCK" >/dev/null || fail "production manifest is not format 2"
ok "production manifest uses format 2"

printf 'format\t1\n' >"$TMP/empty.lock"
expect_failure "empty manifest is rejected" "must contain at least one direct tool" \
  env HARNESS_WORKSPACE_LOCK="$TMP/empty.lock" "$SCRIPT" validate-lock

assert_pin common uv 0.12.5 archive
assert_pin common python 3.13.15 uv-python
assert_pin common lizard 1.22.2 pypi-wheel
assert_pin python vulture 2.16 pypi-wheel
assert_pin python pip-audit 2.10.1 pypi-wheel
assert_pin bun bun 1.3.14 archive
assert_pin bun knip 5.88.1 npm-package
assert_pin go go 1.27.0 archive
assert_pin go golangci-lint 2.12.2 archive
assert_pin go govulncheck 1.1.4 go-module
assert_pin go go-arch-lint 1.15.0 go-module
assert_pin go gremlins 0.5.0 go-module
assert_pin rust rustup 1.28.2 archive
assert_pin rust rust 1.97.1 rustup-toolchain
assert_pin rust cargo-audit 0.22.2 archive
assert_pin rust cargo-llvm-cov 0.8.7 archive
assert_pin rust cargo-modules 0.26.0 cargo-crate
ok "all approved versions are pinned"

assert_probe lizard --version 1.22.2
assert_probe vulture --version 'vulture 2.16'
assert_probe pip-audit --version 'pip-audit 2.10.1'
ok "Python CLI probes match their real exact output"

assert_probe govulncheck --version 'Scanner: govulncheck@v1.1.4'
assert_probe go-arch-lint version 'Linter version:'
assert_probe gremlins --version 'gremlins version dev'
assert_source govulncheck golang.org/x/vuln/cmd/govulncheck@v1.1.4
assert_source go-arch-lint github.com/fe3dback/go-arch-lint@v1.15.0
assert_source gremlins github.com/go-gremlins/gremlins/cmd/gremlins@v0.5.0
ok "Go CLI probes and authoritative module identities are exact"

assert_input common uv .harness/python-downloads.json 8ec7cc98550d812b7aa854173e86c4d73f4ab452abb6deb59360a96077d64422
assert_input common uv .harness/python-tools.lock d65d7ba9ac7958c0aacd21a0f7c5fe7106eb15f36179fea052307236b57f89c6
assert_input bun bun .harness/bun-tools/package.json b69e87c3e1d9cdfd185fc58ba9b3d397fc9e7532ccb902cbd1cee58c273cef13
assert_input bun bun .harness/bun-tools/bun.lock 699319fa81f7918cf9eb42ad73de4552db86e89223df617fa1c93c26f9ed887d
assert_input go go .harness/go-tools/go.mod 5404e63dcf741e41213c0ff662a7d84302c1e730e77495b2f6dca57d7f5760e5
assert_input go go .harness/go-tools/go.sum f3e470b7c105dfa570b32616ca36b023dc479ceae8466ed04271880a4c73f5c7
assert_input rust cargo .harness/rust-dist.lock 47ad3f93fd0a6ce443a2bdaf67b427b038dfb745b8bb9c9dda609be959518f25
assert_input rust cargo .harness/cargo-modules.lock 4ce064288b714b3e9c080126e3b3db01c91b6d4665c93b57258fe091c02dcba2
ok "all ecosystem-native locks are bound by exact digests"

bad=$(copy_lock bad-header)
awk 'BEGIN { FS = OFS = "\t" } $1 == "format" && !done { $2 = "3"; done = 1 } { print }' "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "unknown format is rejected" "unsupported lock format" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

v2=$(copy_lock format-2)
convert_to_v2 "$v2" "$v2.new"
mv "$v2.new" "$v2"
expect_success "format 2 preserves unkeyed artifact behavior" \
  env HARNESS_WORKSPACE_LOCK="$v2" "$SCRIPT" validate-lock

source_sha=$(file_sha256 "$SCRIPT")
with_inputs=$(copy_lock format-2-inputs)
convert_to_v2 "$with_inputs" "$with_inputs.new"
mv "$with_inputs.new" "$with_inputs"
for spec in 'common uv' 'bun bun' 'go go' 'rust cargo'; do
  set -- $spec
  printf 'input\t%s\t%s\t.harness/workspace.sh\t%s\n' "$1" "$2" "$source_sha" >>"$with_inputs"
done
expect_success "format 2 accepts hashed ecosystem inputs" \
  env HARNESS_WORKSPACE_LOCK="$with_inputs" "$SCRIPT" validate-lock

bad=$(copy_lock input-under-format-1)
convert_to_v1 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\t%s\n' "$source_sha" >>"$bad"
expect_failure "format 1 rejects input records" "input record requires lock format 2" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock malformed-input)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\n' >>"$bad"
expect_failure "malformed input is rejected" "malformed input record" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock unknown-input-profile)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tunknown\tuv\t.harness/workspace.sh\t%s\n' "$source_sha" >>"$bad"
expect_failure "unknown input profile is rejected" "unknown input profile 'unknown'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock unknown-input-ecosystem)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tpip\t.harness/workspace.sh\t%s\n' "$source_sha" >>"$bad"
expect_failure "unknown input ecosystem is rejected" "unknown input ecosystem 'pip'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock unsafe-input)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t../workspace.lock\t%s\n' "$source_sha" >>"$bad"
expect_failure "unsafe input path is rejected" "unsafe input path" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock invalid-input-sha)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\tABC\n' >>"$bad"
expect_failure "invalid input SHA is rejected" "invalid input SHA-256" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock duplicate-input)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\t%s\n' "$source_sha" >>"$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\t%s\n' "$source_sha" >>"$bad"
expect_failure "duplicate input is rejected" "duplicate input 'common/uv/.harness/workspace.sh'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock missing-input)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/missing.lock\t%s\n' "$source_sha" >>"$bad"
expect_failure "missing input is rejected" "input is missing or unreadable" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock mismatched-input)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'input\tcommon\tuv\t.harness/workspace.sh\t%s\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$bad"
expect_failure "input checksum mismatch is rejected" "input checksum mismatch" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

keyed=$(copy_lock keyed-artifacts)
convert_to_v2 "$keyed" "$keyed.new"
mv "$keyed.new" "$keyed"
printf 'artifact\trust\trustc\tlinux-x86_64\thttps://example.com/rustc\t%s\ttar.xz\trustc.tar.xz\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$keyed"
printf 'artifact\trust\tcargo\tlinux-x86_64\thttps://example.com/cargo\t%s\ttar.xz\tcargo.tar.xz\n' \
  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' >>"$keyed"
expect_success "format 2 accepts distinct keyed artifacts for one platform" \
  env HARNESS_WORKSPACE_LOCK="$keyed" "$SCRIPT" validate-lock

bad=$(copy_lock duplicate-keyed-artifact)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'artifact\trust\trustc\tlinux-x86_64\thttps://example.com/rustc\t%s\ttar.xz\trustc.tar.xz\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$bad"
printf 'artifact\trust\trustc\tlinux-x86_64\thttps://example.com/rustc-copy\t%s\ttar.xz\trustc-copy.tar.xz\n' \
  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' >>"$bad"
expect_failure "duplicate keyed artifact is rejected" "duplicate artifact 'rust/rustc/linux-x86_64'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock invalid-artifact-key)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'artifact\trust\tBAD_KEY\tlinux-x86_64\thttps://example.com/rustc\t%s\ttar.xz\trustc.tar.xz\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$bad"
expect_failure "invalid artifact key is rejected" "invalid artifact key 'BAD_KEY'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock keyed-direct-artifact)
convert_to_v2 "$bad" "$bad.new"
mv "$bad.new" "$bad"
printf 'artifact\tuv\textra\tlinux-x86_64\thttps://example.com/uv-extra\t%s\traw\tuv-extra\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$bad"
expect_failure "direct tools reject keyed artifacts" "direct tool 'uv' must not use keyed artifacts" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock bad-fields)
printf 'tool\tcommon\tbroken\n' >>"$bad"
expect_failure "wrong field count is rejected" "malformed tool record" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock bad-profile)
awk 'BEGIN { FS = OFS = "\t" } $1 == "tool" && $2 == "common" && $3 == "uv" && !done { $2 = "unknown"; done = 1 } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "unknown profile is rejected" "unknown profile 'unknown'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock no-common-tool)
awk 'BEGIN { FS = OFS = "\t" } $1 == "tool" && $2 == "common" { $2 = "python" } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "common direct tool is required" "must contain a common direct tool" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock duplicate)
awk -F '\t' '$1 == "artifact" && $2 == "uv" && $4 == "darwin-x86_64" { print; exit }' "$LOCK" >>"$bad"
expect_failure "duplicate artifact is rejected" "duplicate artifact 'uv/-/darwin-x86_64'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock bad-sha)
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" && $2 == "uv" && $4 == "darwin-x86_64" && !done { $6 = "ABC"; done = 1 } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "invalid SHA-256 is rejected" "invalid SHA-256" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock bad-url)
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" && $2 == "uv" && $4 == "darwin-x86_64" && !done { sub(/^https:/, "http:", $5); done = 1 } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "non-HTTPS artifact is rejected" "artifact URL must use https" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock traversal)
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" && $2 == "uv" && $4 == "darwin-x86_64" && !done { $8 = "../uv"; done = 1 } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "unsafe payload path is rejected" "unsafe artifact payload" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock missing-platform)
awk -F '\t' '!( $1 == "artifact" && $2 == "uv" && $4 == "linux-arm64" )' "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "binary tool without four platforms is rejected" "missing artifact 'uv/linux-arm64'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock unsupported-direct-archive)
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" && $2 == "uv" && $4 == "darwin-x86_64" && !done { $7 = "wheel"; done = 1 } { print }' \
  "$bad" >"$bad.new"
mv "$bad.new" "$bad"
expect_failure "direct tool archive must be installable" "uses unsupported install archive 'wheel'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

bad=$(copy_lock orphan)
printf 'artifact\torphan\t-\tany\thttps://example.com/orphan\t%s\traw\torphan\n' \
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' >>"$bad"
expect_failure "orphan artifact is rejected" "artifact references unknown tool or pin 'orphan'" \
  env HARNESS_WORKSPACE_LOCK="$bad" "$SCRIPT" validate-lock

printf '1..%d\n' "$passed"
