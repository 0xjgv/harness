#!/bin/bash
set -euo pipefail

ROOT=$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)
SCRIPT="$ROOT/.harness/workspace.sh"
ORIGINAL_PATH=$PATH
REAL_CURL=$(command -v curl)
REAL_MV=$(command -v mv)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/harness-workspace.XXXXXX")
trap '[ "${KEEP_WORKSPACE_TEST_TMP:-0}" = 1 ] || rm -rf "$TMP"' EXIT

passed=0
CASE_ROOT=
CASE_HOME=
CASE_LOCK=
CASE_TOOLS=
CASE_CACHE=
CASE_ARTIFACT=
CASE_CURL_LOG=
CASE_UV_LOG=
CASE_BUN_ARTIFACT=
CASE_BUN_LOG=
CASE_GO_ARTIFACT=
CASE_GO_LOG=
CASE_RUST_ARCHIVES=
CASE_RUST_INIT_LOG=
CASE_RUSTUP_LOG=
CASE_CARGO_CRATE=
CASE_CARGO_LOG=
CASE_RUST_PLATFORM=
CASE_RUST_TARGET=
CASE_PATH=
CASE_HTTPS_ARTIFACT=

ok() {
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$passed" "$1"
}

fail() {
  printf 'not ok - %s\n' "$1" >&2
  [ ! -f "$TMP/output" ] || sed 's/^/  /' "$TMP/output" >&2
  exit 1
}

expect_ok() {
  local name=$1
  shift
  if "$@" >"$TMP/output" 2>&1; then
    ok "$name"
  else
    fail "$name"
  fi
}

expect_fail() {
  local name=$1 expected=$2
  shift 2
  if "$@" >"$TMP/output" 2>&1; then
    fail "$name (unexpected success)"
  fi
  if ! grep -F "$expected" "$TMP/output" >/dev/null; then
    fail "$name (missing: $expected)"
  fi
  ok "$name"
}

expect_rejected() {
  local name=$1
  shift
  if "$@" >"$TMP/output" 2>&1; then
    fail "$name (unexpected success)"
  fi
  ok "$name"
}

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

tree_has_sha() {
  local root=$1 expected=$2 candidate
  [ -d "$root" ] || return 1
  while IFS= read -r candidate; do
    [ "$(sha_of "$candidate")" != "$expected" ] || return 0
  done < <(find "$root" -type f -print)
  return 1
}

mode_of() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

managed_hook_is_exact() {
  local file=$1 name=$2
  cmp -s "$file" <(
    printf '#!/bin/sh\nset -eu\nroot=$(git rev-parse --show-toplevel)\ncd "$root"\nexec make %s\n' "$name"
  )
}

write_lock() {
  local destination=$1 artifact=$2 sha=$3 archive=${4:-raw} payload=${5:-probe}
  {
    printf 'format\t1\n'
    printf 'tool\tcommon\tprobe\t1.0.0\tarchive\tbin/probe\t--version\tprobe 1.0.0\t-\n'
    printf 'artifact\tprobe\tdarwin-x86_64\tfile://%s\t%s\t%s\t%s\n' "$artifact" "$sha" "$archive" "$payload"
    printf 'artifact\tprobe\tdarwin-arm64\tfile://%s\t%s\t%s\t%s\n' "$artifact" "$sha" "$archive" "$payload"
    printf 'artifact\tprobe\tlinux-x86_64\tfile://%s\t%s\t%s\t%s\n' "$artifact" "$sha" "$archive" "$payload"
    printf 'artifact\tprobe\tlinux-arm64\tfile://%s\t%s\t%s\t%s\n' "$artifact" "$sha" "$archive" "$payload"
    printf 'pin\tcommon\tpython\t3.13.15\tuv-python\t-\t--version\tPython 3.13.15\tcpython-3.13.15\n'
    printf 'pin\trust\trust\t1.97.1\trustup-toolchain\t-\t--version\trustc 1.97.1\t1.97.1\n'
  } >"$destination"
}

new_fixture() {
  local name=$1 base sha
  base=$TMP/$name
  CASE_ROOT=$base/repo
  CASE_HOME=$base/home
  CASE_LOCK=$CASE_ROOT/.harness/workspace.lock
  CASE_TOOLS=$CASE_HOME/.local/share/harness/tools
  CASE_CACHE=$CASE_HOME/.cache/harness
  CASE_ARTIFACT=$base/releases/probe
  CASE_CURL_LOG=$base/curl.log
  CASE_UV_LOG=$base/uv.log
  CASE_BUN_ARTIFACT=$base/releases/bun
  CASE_BUN_LOG=$base/bun.log
  CASE_GO_ARTIFACT=$base/releases/go
  CASE_GO_LOG=$base/go.log
  CASE_RUST_ARCHIVES=$base/releases/rust
  CASE_RUST_INIT_LOG=$base/rust-init.log
  CASE_RUSTUP_LOG=$base/rustup.log
  CASE_CARGO_CRATE=$base/releases/cargo-modules-0.26.0.crate
  CASE_CARGO_LOG=$base/cargo.log
  CASE_RUST_PLATFORM=
  CASE_RUST_TARGET=
  CASE_PATH=$base/bin:$ORIGINAL_PATH
  CASE_HTTPS_ARTIFACT=$CASE_ARTIFACT
  mkdir -p "$CASE_ROOT/.harness" "$CASE_ROOT/.claude" "$CASE_ROOT/.codex/hooks" \
    "$CASE_ROOT/skills/harness/reference" "$CASE_HOME" "$base/releases" "$base/bin"
  cat >"$CASE_ARTIFACT" <<'EOF'
#!/bin/sh
case ${1:-} in
  --version) printf '%s\n' 'probe 1.0.0' ;;
  env)
    printf 'PATH=%s\n' "$PATH"
    printf 'UV_CACHE_DIR=%s\n' "$UV_CACHE_DIR"
    printf 'UV_PYTHON_INSTALL_DIR=%s\n' "$UV_PYTHON_INSTALL_DIR"
    printf 'BUN_INSTALL_CACHE_DIR=%s\n' "$BUN_INSTALL_CACHE_DIR"
    printf 'GOMODCACHE=%s\n' "$GOMODCACHE"
    printf 'GOCACHE=%s\n' "$GOCACHE"
    printf 'CARGO_HOME=%s\n' "$CARGO_HOME"
    printf 'RUSTUP_HOME=%s\n' "$RUSTUP_HOME"
    printf 'UV_OFFLINE=%s\n' "${UV_OFFLINE:-0}"
    printf 'GOPROXY=%s\n' "${GOPROXY:-}"
    printf 'CARGO_NET_OFFLINE=%s\n' "${CARGO_NET_OFFLINE:-}"
    ;;
  *) printf '%s\n' 'probe invoked' ;;
esac
EOF
  chmod +x "$CASE_ARTIFACT"
  sha=$(sha_of "$CASE_ARTIFACT")
  write_lock "$CASE_LOCK" "$CASE_ARTIFACT" "$sha"
  cat >"$CASE_ROOT/.claude/settings.json" <<'EOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "cd $CLAUDE_PROJECT_DIR && make stop-hook"}]}]}
}
EOF
  cat >"$CASE_ROOT/.codex/hooks.json" <<'EOF'
{
  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "cd \"$(git rev-parse --show-toplevel)\" && .codex/hooks/codex-stop-hook.sh make stop-hook"}]}]}
}
EOF
  cp "$ROOT/.codex/hooks/codex-stop-hook.sh" "$CASE_ROOT/.codex/hooks/codex-stop-hook.sh"
  printf '%s\n' '# fixture harness skill' >"$CASE_ROOT/skills/harness/SKILL.md"
  printf '%s\n' 'fixture reference' >"$CASE_ROOT/skills/harness/reference/example.md"
  cat >"$CASE_ROOT/Makefile" <<'EOF'
.PHONY: pre-commit pre-push stop-hook
pre-commit:
	@:
pre-push:
	@/bin/cat > "$$PREPUSH_CAPTURE"
stop-hook:
	@:
EOF
  cat >"$base/bin/curl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$CURL_LOG"
exec "$REAL_CURL" "$@"
EOF
  chmod +x "$base/bin/curl"
  git -C "$CASE_ROOT" init -q
  git -C "$CASE_ROOT" config user.name 'Workspace Test'
  git -C "$CASE_ROOT" config user.email 'workspace@example.test'
  git -C "$CASE_ROOT" config core.hooksPath "$CASE_ROOT/.git/hooks"
  git -C "$CASE_ROOT" add .
  git -C "$CASE_ROOT" commit -qm fixture
}

write_python_lock() {
  local destination=$1 artifact=$2 artifact_sha=$3 input_sha=$4 platform
  {
    printf 'format\t2\n'
    printf 'input\tcommon\tuv\t.harness/python-downloads.json\t%s\n' "$input_sha"
    printf 'tool\tcommon\tuv\t0.12.5\tarchive\tbin/uv\t--version\tuv 0.12.5\t-\n'
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tuv\t-\t%s\tfile://%s\t%s\traw\tuv\n' \
        "$platform" "$artifact" "$artifact_sha"
    done
    printf 'pin\tcommon\tpython\t3.13.15\tuv-python\t-\t--version\tPython 3.13.15\tcpthon-3.13.15\n'
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tpython\t-\t%s\tfile://%s\t%s\traw\tpython\n' \
        "$platform" "$artifact" "$artifact_sha"
    done
  } >"$destination"
}

new_python_fixture() {
  local name=$1 artifact_sha input_sha
  new_fixture "$name"
  cat >"$CASE_ARTIFACT" <<'EOF'
#!/bin/sh
set -eu

if [ "${1:-}" = --version ]; then
  printf '%s\n' 'uv 0.12.5'
  exit 0
fi

printf '%s\t%s\t%s\n' "$0" "${UV_CACHE_DIR:-}" "$*" >>"$UV_LOG"
[ "${UV_CACHE_DIR:-}" = "$EXPECTED_UV_CACHE" ] || exit 75

handle_python_install() {
  shift 2
  install_dir=
  downloads=
  version=
  no_bin=0
  managed=0
  no_config=0
  while [ "$#" -gt 0 ]; do
    case $1 in
      --no-bin) no_bin=1; shift ;;
      --managed-python) managed=1; shift ;;
      --no-config) no_config=1; shift ;;
      --python-downloads-json-url) downloads=${2:-}; shift 2 ;;
      --install-dir) install_dir=${2:-}; shift 2 ;;
      3.13.15) version=$1; shift ;;
      *) exit 71 ;;
    esac
  done
  [ "$no_bin" -eq 1 ] && [ "$managed" -eq 1 ] && [ "$no_config" -eq 1 ] || exit 72
  [ "$downloads" = "$EXPECTED_PYTHON_DOWNLOADS" ] || exit 73
  [ "$version" = 3.13.15 ] && [ -n "$install_dir" ] || exit 74

  create_runtime() {
    runtime=$install_dir/cpython-3.13.15-test-$1
    mkdir -p "$runtime/bin"
    cat >"$runtime/bin/python3.13" <<'PYTHON'
#!/bin/sh
[ "${1:-}" = --version ] || exit 64
printf '%s\n' 'Python 3.13.15'
PYTHON
    chmod +x "$runtime/bin/python3.13"
  }

  case ${FAKE_UV_RUNTIME_MODE:-one} in
    one)
      create_runtime one
      ln -s "$runtime" "$install_dir/cpython-3.13-test-alias"
      ;;
    two)
      create_runtime one
      first_runtime=$runtime
      create_runtime two
      ln -s "$first_runtime" "$install_dir/cpython-3.13-test-alias"
      ;;
    zero) : ;;
    *) exit 76 ;;
  esac
}

handle_venv() {
  shift
  destination=
  python=
  no_config=0
  no_downloads=0
  while [ "$#" -gt 0 ]; do
    case $1 in
      --offline) shift ;;
      --no-config) no_config=1; shift ;;
      --no-python-downloads) no_downloads=1; shift ;;
      --python) python=${2:-}; shift 2 ;;
      -*) exit 77 ;;
      *) destination=$1; shift ;;
    esac
  done
  [ "$no_config" -eq 1 ] && [ "$no_downloads" -eq 1 ] || exit 78
  [ "$python" = "$EXPECTED_MANAGED_PYTHON" ] && [ -n "$destination" ] || exit 79
  mkdir -p "$destination/bin"
  cat >"$destination/bin/python" <<'PYTHON_CLI'
#!/bin/sh
[ "${1:-}" = -m ] || exit 80
module=${2:-}
shift 2
[ "${1:-}" = --version ] || exit 81
case $module in
  lizard) printf '%s\n' '1.22.2' ;;
  vulture) printf '%s\n' 'vulture 2.16' ;;
  pip_audit) printf '%s\n' 'pip-audit 2.10.1' ;;
  *) exit 82 ;;
esac
PYTHON_CLI
  chmod +x "$destination/bin/python"
}

handle_pip_install() {
  shift 2
  python=
  constraint=
  requirement=
  offline=0
  require_hashes=0
  no_build=0
  no_config=0
  no_downloads=0
  while [ "$#" -gt 0 ]; do
    case $1 in
      --offline) offline=1; shift ;;
      --no-config) no_config=1; shift ;;
      --no-python-downloads) no_downloads=1; shift ;;
      --python) python=${2:-}; shift 2 ;;
      --require-hashes) require_hashes=1; shift ;;
      --no-build) no_build=1; shift ;;
      --constraint) constraint=${2:-}; shift 2 ;;
      lizard==1.22.2|vulture==2.16|pip-audit==2.10.1) requirement=$1; shift ;;
      *) exit 84 ;;
    esac
  done
  [ "$require_hashes" -eq 1 ] && [ "$no_build" -eq 1 ] || exit 85
  [ "$no_config" -eq 1 ] && [ "$no_downloads" -eq 1 ] || exit 86
  [ -x "$python" ] && [ "$constraint" = "$EXPECTED_PYTHON_TOOLS" ] && [ -n "$requirement" ] || exit 87
  [ "${FAKE_UV_PIP_MODE:-ok}" = ok ] || exit 88
  if [ "$offline" -eq 1 ] && [ "${FAKE_UV_OFFLINE_CACHE:-warm}" != warm ]; then
    exit 89
  fi
}

case ${1:-}:${2:-} in
  python:install) handle_python_install "$@" ;;
  venv:*) handle_venv "$@" ;;
  pip:install) handle_pip_install "$@" ;;
  *) exit 70 ;;
esac
EOF
  chmod +x "$CASE_ARTIFACT"
  cp "$ROOT/.harness/python-downloads.json" "$CASE_ROOT/.harness/python-downloads.json"
  artifact_sha=$(sha_of "$CASE_ARTIFACT")
  input_sha=$(sha_of "$CASE_ROOT/.harness/python-downloads.json")
  write_python_lock "$CASE_LOCK" "$CASE_ARTIFACT" "$artifact_sha" "$input_sha"
  git -C "$CASE_ROOT" add .harness
  git -C "$CASE_ROOT" commit -qm python-fixture
}

new_python_cli_fixture() {
  local name=$1 tools_sha artifact_sha
  new_python_fixture "$name"
  cp "$ROOT/.harness/python-tools.lock" "$CASE_ROOT/.harness/python-tools.lock"
  tools_sha=$(sha_of "$CASE_ROOT/.harness/python-tools.lock")
  artifact_sha=$(sha_of "$CASE_ARTIFACT")
  {
    printf 'input\tcommon\tuv\t.harness/python-tools.lock\t%s\n' "$tools_sha"
    printf 'pin\tcommon\tlizard\t1.22.2\tpypi-wheel\t-\t--version\t1.22.2\tlizard==1.22.2\n'
    printf 'artifact\tlizard\t-\tany\tfile://%s\t%s\traw\tlizard.whl\n' \
      "$CASE_ARTIFACT" "$artifact_sha"
    printf 'pin\tpython\tvulture\t2.16\tpypi-wheel\t-\t--version\tvulture 2.16\tvulture==2.16\n'
    printf 'artifact\tvulture\t-\tany\tfile://%s\t%s\traw\tvulture.whl\n' \
      "$CASE_ARTIFACT" "$artifact_sha"
    printf 'pin\tpython\tpip-audit\t2.10.1\tpypi-wheel\t-\t--version\tpip-audit 2.10.1\tpip-audit==2.10.1\n'
    printf 'artifact\tpip-audit\t-\tany\tfile://%s\t%s\traw\tpip-audit.whl\n' \
      "$CASE_ARTIFACT" "$artifact_sha"
  } >>"$CASE_LOCK"
  git -C "$CASE_ROOT" add .harness
  git -C "$CASE_ROOT" commit -qm python-cli-fixture
}

new_bun_fixture() {
  local name=$1 bun_sha package_sha lock_sha platform
  new_python_cli_fixture "$name"
  cat >"$CASE_BUN_ARTIFACT" <<'EOF'
#!/bin/sh
set -eu

if [ "${1:-}" = --version ]; then
  printf '%s\n' '1.3.14'
  exit 0
fi
case ${1:-} in
  */node_modules/knip/bin/knip.js)
    [ -f "$1" ] || exit 90
    shift
    [ "${1:-}" = --version ] || exit 91
    printf '%s\n' '5.88.1'
    exit 0
    ;;
esac

[ "${1:-}" = install ] || exit 92
printf '%s\t%s\t%s\n' "$0" "${BUN_INSTALL_CACHE_DIR:-}" "$*" >>"$BUN_LOG"
shift
cwd=
offline=0
frozen=0
ignored=0
copyfile=0
hoisted=0
no_progress=0
no_summary=0
while [ "$#" -gt 0 ]; do
  case $1 in
    --offline) offline=1; shift ;;
    --cwd) cwd=${2:-}; shift 2 ;;
    --frozen-lockfile) frozen=1; shift ;;
    --ignore-scripts) ignored=1; shift ;;
    --backend) [ "${2:-}" = copyfile ] || exit 93; copyfile=1; shift 2 ;;
    --linker) [ "${2:-}" = hoisted ] || exit 94; hoisted=1; shift 2 ;;
    --no-progress) no_progress=1; shift ;;
    --no-summary) no_summary=1; shift ;;
    *) exit 95 ;;
  esac
done
[ "${BUN_INSTALL_CACHE_DIR:-}" = "$EXPECTED_BUN_CACHE" ] || exit 96
[ "$frozen" -eq 1 ] && [ "$ignored" -eq 1 ] && [ "$copyfile" -eq 1 ] && [ "$hoisted" -eq 1 ] || exit 97
[ "$no_progress" -eq 1 ] && [ "$no_summary" -eq 1 ] && [ -n "$cwd" ] || exit 98
cmp -s "$cwd/package.json" "$EXPECTED_BUN_PACKAGE" || exit 99
cmp -s "$cwd/bun.lock" "$EXPECTED_BUN_LOCK" || exit 100
[ "${FAKE_BUN_INSTALL_MODE:-ok}" = ok ] || exit 101
if [ "$offline" -eq 1 ] && [ "${FAKE_BUN_OFFLINE_CACHE:-warm}" != warm ]; then
  exit 102
fi
if [ "${FAKE_BUN_PAYLOAD_MODE:-present}" = present ]; then
  mkdir -p "$cwd/node_modules/knip/bin"
  printf '%s\n' '// exact fake Knip entrypoint' >"$cwd/node_modules/knip/bin/knip.js"
fi
EOF
  chmod +x "$CASE_BUN_ARTIFACT"
  mkdir -p "$CASE_ROOT/.harness/bun-tools"
  cp "$ROOT/.harness/bun-tools/package.json" "$CASE_ROOT/.harness/bun-tools/package.json"
  cp "$ROOT/.harness/bun-tools/bun.lock" "$CASE_ROOT/.harness/bun-tools/bun.lock"
  bun_sha=$(sha_of "$CASE_BUN_ARTIFACT")
  package_sha=$(sha_of "$CASE_ROOT/.harness/bun-tools/package.json")
  lock_sha=$(sha_of "$CASE_ROOT/.harness/bun-tools/bun.lock")
  {
    printf 'input\tbun\tbun\t.harness/bun-tools/package.json\t%s\n' "$package_sha"
    printf 'input\tbun\tbun\t.harness/bun-tools/bun.lock\t%s\n' "$lock_sha"
    printf 'tool\tbun\tbun\t1.3.14\tarchive\tbin/bun\t--version\t1.3.14\t-\n'
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tbun\t-\t%s\tfile://%s\t%s\traw\tbun\n' \
        "$platform" "$CASE_BUN_ARTIFACT" "$bun_sha"
    done
    printf 'pin\tbun\tknip\t5.88.1\tnpm-package\t-\t--version\t5.88.1\tknip@5.88.1\n'
    printf 'artifact\tknip\t-\tany\tfile://%s\t%s\traw\tknip.tgz\n' \
      "$CASE_BUN_ARTIFACT" "$bun_sha"
  } >>"$CASE_LOCK"
  git -C "$CASE_ROOT" add .harness
  git -C "$CASE_ROOT" commit -qm bun-fixture
}

new_go_fixture() {
  local name=$1 go_sha golangci_sha mod_sha sum_sha platform golangci_lint
  new_python_cli_fixture "$name"
  cat >"$CASE_GO_ARTIFACT" <<'EOF'
#!/bin/sh
set -eu

if [ "${1:-}" = version ] && [ "${2:-}" != -m ]; then
  printf '%s\n' 'go version go1.27.0 test/arch'
  exit 0
fi
if [ "${1:-}" = version ] && [ "${2:-}" = -m ]; then
  executable=${3:-}
  name=${executable##*/}
  case $name in
    govulncheck)
      package=golang.org/x/vuln/cmd/govulncheck
      module=golang.org/x/vuln
      version=v1.1.4
      module_sum='h1:Ju8QsuyhX3Hk8ma3CesTbO8vfJD9EvUBgHvkxHBzj0I='
      ;;
    go-arch-lint)
      package=github.com/fe3dback/go-arch-lint
      module=github.com/fe3dback/go-arch-lint
      version=v1.15.0
      module_sum='h1:+csfU0F5yxWNGjXjj2dufSvdVOMsGr+Hef7EiBwn6U8='
      ;;
    gremlins)
      package=github.com/go-gremlins/gremlins/cmd/gremlins
      module=github.com/go-gremlins/gremlins
      version=v0.5.0
      module_sum='h1:fn1I5/Yj483PbPCvY3FvL530xHMQn8b1oDdP4FJ2gUw='
      ;;
    *) exit 110 ;;
  esac
  if [ "${FAKE_GO_METADATA_MODE:-exact}" != exact ]; then
    version=v0.0.0
  fi
  printf '%s: go1.27.0\n' "$executable"
  printf '\tpath\t%s\n' "$package"
  printf '\tmod\t%s\t%s\t%s\n' "$module" "$version" "$module_sum"
  exit 0
fi

[ "${1:-}" = install ] && [ "${2:-}" = -mod=readonly ] || exit 111
package=${3:-}
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$0" "$PWD" "$GOBIN" "$GOMODCACHE" "$GOCACHE" "$GOENV/$GOWORK/$GOTOOLCHAIN" \
  "$CGO_ENABLED" "$GOPROXY" "$*" >>"$GO_LOG"
[ "$GOROOT" = "$EXPECTED_GOROOT" ] || exit 112
[ "$GOMODCACHE" = "$EXPECTED_GOMODCACHE" ] && [ "$GOCACHE" = "$EXPECTED_GOCACHE" ] || exit 113
[ "$GOENV" = off ] && [ "$GOWORK" = off ] && [ "$GOTOOLCHAIN" = local ] || exit 114
[ "$GOFLAGS" = -mod=readonly ] && [ "$CGO_ENABLED" = 0 ] || exit 115
cmp -s "$PWD/go.mod" "$EXPECTED_GO_MOD" || exit 116
cmp -s "$PWD/go.sum" "$EXPECTED_GO_SUM" || exit 117
[ "${FAKE_GO_BUILD_MODE:-ok}" = ok ] || exit 118
if [ "$GOPROXY" = off ] && [ "${FAKE_GO_OFFLINE_CACHE:-warm}" != warm ]; then
  exit 119
fi
case $package in
  golang.org/x/vuln/cmd/govulncheck) name=govulncheck ;;
  github.com/fe3dback/go-arch-lint) name=go-arch-lint ;;
  github.com/go-gremlins/gremlins/cmd/gremlins) name=gremlins ;;
  *) exit 120 ;;
esac
if [ "${FAKE_GO_PAYLOAD_MODE:-present}" = present ]; then
  mkdir -p "$GOBIN"
  case $name in
    govulncheck)
      printf '%s\n' '#!/bin/sh' '[ "${1:-}" = --version ] || exit 121' \
        "printf '%s\\n' 'Scanner: govulncheck@v1.1.4'" >"$GOBIN/$name"
      ;;
    go-arch-lint)
      printf '%s\n' '#!/bin/sh' '[ "${1:-}" = version ] || exit 122' \
        "printf '%s\\n' 'Linter version: v1.15.0'" >"$GOBIN/$name"
      ;;
    gremlins)
      printf '%s\n' '#!/bin/sh' '[ "${1:-}" = --version ] || exit 123' \
        "printf '%s\\n' 'gremlins version dev test/arch'" >"$GOBIN/$name"
      ;;
  esac
  chmod +x "$GOBIN/$name"
fi
EOF
  chmod +x "$CASE_GO_ARTIFACT"
  golangci_lint=${CASE_ROOT%/repo}/releases/golangci-lint
  cat >"$golangci_lint" <<'EOF'
#!/bin/sh
[ "${1:-}" = --version ] || exit 109
printf '%s\n' 'golangci-lint has version 2.12.2 built with go1.27.0'
EOF
  chmod +x "$golangci_lint"
  mkdir -p "$CASE_ROOT/.harness/go-tools"
  cp "$ROOT/.harness/go-tools/go.mod" "$CASE_ROOT/.harness/go-tools/go.mod"
  cp "$ROOT/.harness/go-tools/go.sum" "$CASE_ROOT/.harness/go-tools/go.sum"
  go_sha=$(sha_of "$CASE_GO_ARTIFACT")
  golangci_sha=$(sha_of "$golangci_lint")
  mod_sha=$(sha_of "$CASE_ROOT/.harness/go-tools/go.mod")
  sum_sha=$(sha_of "$CASE_ROOT/.harness/go-tools/go.sum")
  {
    printf 'input\tgo\tgo\t.harness/go-tools/go.mod\t%s\n' "$mod_sha"
    printf 'input\tgo\tgo\t.harness/go-tools/go.sum\t%s\n' "$sum_sha"
    printf 'tool\tgo\tgo\t1.27.0\tarchive\tgo/bin/go\tversion\tgo version go1.27.0\t-\n'
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tgo\t-\t%s\tfile://%s\t%s\traw\tgo\n' \
        "$platform" "$CASE_GO_ARTIFACT" "$go_sha"
    done
    printf 'tool\tgo\tgolangci-lint\t2.12.2\tarchive\tbin/golangci-lint\t--version\tgolangci-lint has version 2.12.2\t-\n'
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tgolangci-lint\t-\t%s\tfile://%s\t%s\traw\tgolangci-lint\n' \
        "$platform" "$golangci_lint" "$golangci_sha"
    done
    printf 'pin\tgo\tgovulncheck\t1.1.4\tgo-module\t-\t--version\tScanner: govulncheck@v1.1.4\tgolang.org/x/vuln/cmd/govulncheck@v1.1.4\n'
    printf 'pin\tgo\tgo-arch-lint\t1.15.0\tgo-module\t-\tversion\tLinter version:\tgithub.com/fe3dback/go-arch-lint@v1.15.0\n'
    printf 'pin\tgo\tgremlins\t0.5.0\tgo-module\t-\t--version\tgremlins version dev\tgithub.com/go-gremlins/gremlins/cmd/gremlins@v0.5.0\n'
  } >>"$CASE_LOCK"
  git -C "$CASE_ROOT" add .harness
  git -C "$CASE_ROOT" commit -qm go-fixture
}

new_rust_fixture() {
  local name=$1 base platform target component component_root archive
  local manifest manifest_sha sidecar sidecar_sha dist_sha cargo_lock_sha crate_sha
  local rustup_init cargo_audit cargo_llvm_cov tool_sha package_root
  new_python_cli_fixture "$name"
  base=${CASE_ROOT%/repo}
  mkdir -p "$CASE_RUST_ARCHIVES" "$CASE_ROOT/.harness"
  case $("$SCRIPT" platform) in
    darwin-x86_64) CASE_RUST_PLATFORM=darwin-x86_64; CASE_RUST_TARGET=x86_64-apple-darwin ;;
    darwin-arm64) CASE_RUST_PLATFORM=darwin-arm64; CASE_RUST_TARGET=aarch64-apple-darwin ;;
    linux-x86_64) CASE_RUST_PLATFORM=linux-x86_64; CASE_RUST_TARGET=x86_64-unknown-linux-gnu ;;
    linux-arm64) CASE_RUST_PLATFORM=linux-arm64; CASE_RUST_TARGET=aarch64-unknown-linux-gnu ;;
    *) fail "Rust fixture platform is unsupported" ;;
  esac

  cat >"$CASE_RUST_ARCHIVES/fake-rustup-proxy" <<'EOF'
#!/bin/sh
set -eu

name=${0##*/}
case $name in
  rustup)
    if [ "${1:-}" = default ]; then
      [ "${2:-}" = "$EXPECTED_RUST_TOOLCHAIN" ] || exit 128
      printf '%s\n' "$EXPECTED_RUST_TOOLCHAIN" >"$RUSTUP_HOME/fake-default-toolchain"
      exit 0
    fi
    if [ "${1:-}" = which ]; then
      [ "${2:-}" = cargo ] && [ "${3:-}" = --toolchain ] && \
        [ "${4:-}" = "$EXPECTED_RUST_TOOLCHAIN" ] || exit 128
      printf '%s\n' "$RUSTUP_HOME/toolchains/$EXPECTED_RUST_TOOLCHAIN/bin/cargo"
      exit 0
    fi
    if [ "${1:-}" = component ] && [ "${2:-}" = list ]; then
      shift 2
      [ "${1:-}" = --installed ] || exit 129
      shift
      [ "${1:-}" = --toolchain ] && [ "${2:-}" = "$EXPECTED_RUST_TOOLCHAIN" ] || exit 129
      for component in cargo clippy llvm-tools rust-std rustc rustfmt; do
        printf '%s-%s\n' "$component" "$EXPECTED_RUST_TARGET"
      done
      exit 0
    fi
    [ "${1:-}" = toolchain ] && [ "${2:-}" = install ] || exit 130
    shift 2
    toolchain=${1:-}
    shift
    profile=
    components=
    while [ "$#" -gt 0 ]; do
      case $1 in
        --profile) profile=${2:-}; shift 2 ;;
        --component)
          components=${components:+$components,}${2:-}
          shift 2
          ;;
        --no-self-update) shift ;;
        *) exit 131 ;;
      esac
    done
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$0" "$RUSTUP_HOME" "${RUSTUP_DIST_SERVER:-}" "$toolchain" "$profile/$components" \
      >>"$RUSTUP_LOG"
    [ "$toolchain" = "$EXPECTED_RUST_TOOLCHAIN" ] || exit 132
    [ "$profile" = minimal ] || exit 133
    for component in clippy rustfmt llvm-tools-preview; do
      case ,$components, in
        *,$component,*) ;;
        *) exit 134 ;;
      esac
    done
    case ${RUSTUP_DIST_SERVER:-} in
      file://*) mirror=${RUSTUP_DIST_SERVER#file://} ;;
      *) exit 135 ;;
    esac
    [ -f "$mirror/dist/channel-rust-1.97.1.toml" ] || exit 136
    [ -f "$mirror/dist/channel-rust-1.97.1.toml.sha256" ] || exit 137
    [ "$(find "$mirror" -type f -print | wc -l | tr -d ' ')" = 2 ] || exit 137
    records=$RUSTUP_HOME/fake-component-records
    awk -F '\t' -v platform="$EXPECTED_RUST_PLATFORM" \
      '$1 == "component" && $2 == platform { print $3 "\t" $5 "\t" $6 }' \
      "$EXPECTED_RUST_DIST_LOCK" >"$records"
    [ "$(wc -l <"$records" | tr -d ' ')" = 6 ] || exit 138
    while IFS='	' read -r component url sha; do
      source=${url#file://}
      [ "$source" != "$url" ] || exit 139
      [ -f "$source" ] || exit 140
      [ -f "$RUSTUP_HOME/downloads/$sha" ] || exit 141
      cmp -s "$source" "$RUSTUP_HOME/downloads/$sha" || exit 142
    done <"$records"
    [ "${FAKE_RUSTUP_INSTALL_MODE:-ok}" = ok ] || exit 144
    destination=$RUSTUP_HOME/toolchains/$toolchain
    mkdir -p "$destination/bin" "$destination/lib/rustlib/$EXPECTED_RUST_TARGET/bin" \
      "$destination/lib/rustlib/$EXPECTED_RUST_TARGET/lib"
    for proxy in cargo rustc rustdoc rustfmt cargo-fmt cargo-clippy clippy-driver; do
      cp "$FAKE_RUSTUP_PROXY_TEMPLATE" "$destination/bin/$proxy"
      chmod +x "$destination/bin/$proxy"
    done
    for llvm_tool in llvm-cov llvm-profdata; do
      cp "$FAKE_RUSTUP_PROXY_TEMPLATE" \
        "$destination/lib/rustlib/$EXPECTED_RUST_TARGET/bin/$llvm_tool"
      chmod +x "$destination/lib/rustlib/$EXPECTED_RUST_TARGET/bin/$llvm_tool"
    done
    printf '%s\n' 'fake rust-std payload' \
      >"$destination/lib/rustlib/$EXPECTED_RUST_TARGET/lib/libstd-fixture.rlib"
    if [ "${FAKE_RUSTUP_PAYLOAD_MODE:-exact}" != exact ]; then
      rm -f "$destination/bin/rustc"
    fi
    ;;
  cargo)
    if [ "${1:-}" = --version ]; then
      printf '%s\n' 'cargo 1.97.1 (fake 2026-07-16)'
      exit 0
    fi
    [ "${1:-}" = install ] || exit 145
    shift
    locked=0
    no_track=0
    force=0
    offline=0
    root=
    package_path=
    while [ "$#" -gt 0 ]; do
      case $1 in
        --locked) locked=1; shift ;;
        --no-track) no_track=1; shift ;;
        --force) force=1; shift ;;
        --offline) offline=1; shift ;;
        --root) root=${2:-}; shift 2 ;;
        --path) package_path=${2:-}; shift 2 ;;
        *) exit 146 ;;
      esac
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$0" "$CARGO_HOME" "$RUSTUP_HOME" "${RUSTDOC:-}" "${CARGO_NET_OFFLINE:-}" \
      "install locked=$locked no-track=$no_track force=$force offline=$offline root=$root path=$package_path" \
      >>"$CARGO_LOG"
    [ "$locked" -eq 1 ] && [ "$no_track" -eq 1 ] && [ "$force" -eq 1 ] || exit 147
    [ -n "$root" ] && [ -n "$package_path" ] || exit 148
    [ "$CARGO_HOME" = "$EXPECTED_CARGO_CACHE" ] || exit 148
    [ "$RUSTUP_HOME" = "$EXPECTED_MANAGED_RUSTUP_HOME" ] || exit 148
    [ "${RUSTDOC:-}" = "$EXPECTED_MANAGED_RUSTDOC" ] || exit 148
    cmp -s "$package_path/Cargo.lock" "$EXPECTED_CARGO_MODULES_LOCK" || exit 149
    if [ "$offline" -eq 1 ]; then
      [ "${CARGO_NET_OFFLINE:-}" = true ] || exit 150
      [ "${FAKE_CARGO_OFFLINE_CACHE:-warm}" = warm ] || exit 151
    fi
    [ "${FAKE_CARGO_BUILD_MODE:-ok}" = ok ] || exit 152
    if [ "${FAKE_CARGO_PAYLOAD_MODE:-exact}" = exact ]; then
      mkdir -p "$root/bin"
      cat >"$root/bin/cargo-modules" <<'CARGO_MODULES'
#!/bin/sh
[ "${1:-}" = --version ] || exit 153
printf '%s\n' 'cargo-modules 0.26.0'
CARGO_MODULES
      chmod +x "$root/bin/cargo-modules"
    fi
    ;;
  rustc)
    case ${1:-} in
      --version) printf '%s\n' 'rustc 1.97.1 (fake 2026-07-16)' ;;
      -vV)
        printf '%s\n' 'rustc 1.97.1 (fake 2026-07-16)' \
          "host: $EXPECTED_RUST_TARGET" 'release: 1.97.1'
        ;;
      --print)
        [ "${2:-}" = sysroot ] || exit 154
        printf '%s\n' "$RUSTUP_HOME/toolchains/$EXPECTED_RUST_TOOLCHAIN"
        ;;
      *) exit 154 ;;
    esac
    ;;
  rustdoc)
    [ "${1:-}" = --version ] || exit 154
    printf '%s\n' 'rustdoc 1.97.1 (fake 2026-07-16)'
    ;;
  rustfmt|cargo-fmt)
    [ "${1:-}" = --version ] || exit 155
    printf '%s\n' 'rustfmt 1.97.1 (fake 2026-07-16)'
    ;;
  cargo-clippy|clippy-driver)
    [ "${1:-}" = --version ] || exit 156
    printf '%s\n' 'clippy 0.1.97 (fake 2026-07-16)'
    ;;
  llvm-cov|llvm-profdata)
    [ "${1:-}" = --version ] || exit 157
    printf '%s\n' 'LLVM version 21.1.0 (fake)'
    ;;
  *) exit 158 ;;
esac
EOF
  chmod +x "$CASE_RUST_ARCHIVES/fake-rustup-proxy"

  rustup_init=$base/releases/rustup-init
  cat >"$rustup_init" <<'EOF'
#!/bin/sh
set -eu
if [ "${1:-}" = --version ]; then
  printf '%s\n' 'rustup-init 1.28.2'
  exit 0
fi
printf '%s\t%s\t%s\t%s\n' "$0" "$CARGO_HOME" "$RUSTUP_HOME" "$*" >>"$RUST_INIT_LOG"
yes=0
no_modify=0
profile=
default_toolchain=
default_host=
while [ "$#" -gt 0 ]; do
  case $1 in
    -y) yes=1; shift ;;
    --no-modify-path) no_modify=1; shift ;;
    --default-host) default_host=${2:-}; shift 2 ;;
    --profile) profile=${2:-}; shift 2 ;;
    --default-toolchain) default_toolchain=${2:-}; shift 2 ;;
    *) exit 159 ;;
  esac
done
[ "$yes" -eq 1 ] && [ "$no_modify" -eq 1 ] || exit 160
[ "$profile" = minimal ] && [ "$default_toolchain" = none ] || exit 161
[ "$default_host" = "$EXPECTED_RUST_TARGET" ] || exit 161
[ "${FAKE_RUSTUP_INIT_MODE:-ok}" = ok ] || exit 162
mkdir -p "$CARGO_HOME/bin" "$RUSTUP_HOME"
for proxy in rustup cargo rustc rustdoc rustfmt cargo-fmt cargo-clippy clippy-driver; do
  cp "$FAKE_RUSTUP_PROXY_TEMPLATE" "$CARGO_HOME/bin/$proxy"
  chmod +x "$CARGO_HOME/bin/$proxy"
done
EOF
  chmod +x "$rustup_init"

  cargo_audit=$base/releases/cargo-audit
  printf '%s\n' '#!/bin/sh' '[ "${1:-}" = --version ] || exit 163' \
    "printf '%s\\n' 'cargo-audit 0.22.2'" >"$cargo_audit"
  chmod +x "$cargo_audit"
  cargo_llvm_cov=$base/releases/cargo-llvm-cov
  printf '%s\n' '#!/bin/sh' \
    '[ "${1:-}" = llvm-cov ] && [ "${2:-}" = --version ] || exit 164' \
    "printf '%s\\n' 'cargo-llvm-cov 0.8.7'" >"$cargo_llvm_cov"
  chmod +x "$cargo_llvm_cov"

  for component in cargo clippy-preview llvm-tools-preview rust-std rustc rustfmt-preview; do
    component_root=$base/rust-component-source/$component-1.97.1-fixture
    archive=$CASE_RUST_ARCHIVES/$component.tar.xz
    mkdir -p "$component_root"
    printf '%s\n' "$component 1.97.1 fake component" >"$component_root/component.txt"
    tar -cJf "$archive" -C "${component_root%/*}" "${component_root##*/}"
  done

  manifest=$CASE_RUST_ARCHIVES/channel-rust-1.97.1.toml
  printf '%s\n' 'manifest-version = "2"' 'date = "2026-07-16"' \
    '[pkg.rust]' 'version = "1.97.1 (fake 2026-07-16)"' >"$manifest"
  manifest_sha=$(sha_of "$manifest")
  sidecar=$manifest.sha256
  printf '%s  %s\n' "$manifest_sha" "${manifest##*/}" >"$sidecar"
  sidecar_sha=$(sha_of "$sidecar")
  {
    printf '%s\n' '# fake harness Rust distribution closure format 1'
    printf 'format\t1\n'
    printf 'release\t1.97.1\t2026-07-16\n'
    printf 'manifest\tfile://%s\t%s\tfile://%s\t%s\n' \
      "$manifest" "$manifest_sha" "$sidecar" "$sidecar_sha"
    for platform in darwin-arm64 darwin-x86_64 linux-arm64 linux-x86_64; do
      case $platform in
        darwin-arm64) target=aarch64-apple-darwin ;;
        darwin-x86_64) target=x86_64-apple-darwin ;;
        linux-arm64) target=aarch64-unknown-linux-gnu ;;
        linux-x86_64) target=x86_64-unknown-linux-gnu ;;
      esac
      for component in cargo clippy-preview llvm-tools-preview rust-std rustc rustfmt-preview; do
        archive=$CASE_RUST_ARCHIVES/$component.tar.xz
        printf 'component\t%s\t%s\t%s\tfile://%s\t%s\n' \
          "$platform" "$component" "$target" "$archive" "$(sha_of "$archive")"
      done
    done
  } >"$CASE_ROOT/.harness/rust-dist.lock"

  cp "$ROOT/.harness/cargo-modules.lock" "$CASE_ROOT/.harness/cargo-modules.lock"
  package_root=$base/cargo-modules-source/cargo-modules-0.26.0
  mkdir -p "$package_root/src"
  cp "$CASE_ROOT/.harness/cargo-modules.lock" "$package_root/Cargo.lock"
  printf '%s\n' '[package]' 'name = "cargo-modules"' 'version = "0.26.0"' \
    'edition = "2021"' >"$package_root/Cargo.toml"
  printf '%s\n' 'fn main() {}' >"$package_root/src/main.rs"
  tar -czf "$CASE_CARGO_CRATE" -C "${package_root%/*}" "${package_root##*/}"

  dist_sha=$(sha_of "$CASE_ROOT/.harness/rust-dist.lock")
  cargo_lock_sha=$(sha_of "$CASE_ROOT/.harness/cargo-modules.lock")
  crate_sha=$(sha_of "$CASE_CARGO_CRATE")
  {
    printf 'input\trust\tcargo\t.harness/rust-dist.lock\t%s\n' "$dist_sha"
    printf 'input\trust\tcargo\t.harness/cargo-modules.lock\t%s\n' "$cargo_lock_sha"
    printf 'tool\trust\trustup\t1.28.2\tarchive\tbin/rustup-init\t--version\trustup-init 1.28.2\t-\n'
    tool_sha=$(sha_of "$rustup_init")
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\trustup\t-\t%s\tfile://%s\t%s\traw\trustup-init\n' \
        "$platform" "$rustup_init" "$tool_sha"
    done
    printf 'pin\trust\trust\t1.97.1\trustup-toolchain\t-\t--version\trustc 1.97.1\t1.97.1\n'
    printf 'artifact\trust\t-\tany\tfile://%s\t%s\tmanifest\tchannel-rust-1.97.1.toml\n' \
      "$manifest" "$manifest_sha"
    printf 'tool\trust\tcargo-audit\t0.22.2\tarchive\tbin/cargo-audit\t--version\tcargo-audit 0.22.2\t-\n'
    tool_sha=$(sha_of "$cargo_audit")
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tcargo-audit\t-\t%s\tfile://%s\t%s\traw\tcargo-audit\n' \
        "$platform" "$cargo_audit" "$tool_sha"
    done
    printf 'tool\trust\tcargo-llvm-cov\t0.8.7\tarchive\tbin/cargo-llvm-cov\t--version\tcargo-llvm-cov 0.8.7\t-\n'
    tool_sha=$(sha_of "$cargo_llvm_cov")
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      printf 'artifact\tcargo-llvm-cov\t-\t%s\tfile://%s\t%s\traw\tcargo-llvm-cov\n' \
        "$platform" "$cargo_llvm_cov" "$tool_sha"
    done
    printf 'pin\trust\tcargo-modules\t0.26.0\tcargo-crate\t-\t--version\tcargo-modules 0.26.0\tcargo-modules@0.26.0\n'
    printf 'artifact\tcargo-modules\t-\tany\tfile://%s\t%s\tcrate\tcargo-modules-0.26.0.crate\n' \
      "$CASE_CARGO_CRATE" "$crate_sha"
  } >>"$CASE_LOCK"
  git -C "$CASE_ROOT" add .harness
  git -C "$CASE_ROOT" commit -qm rust-fixture
}

workspace() {
  HOME="$CASE_HOME" \
  PATH="$CASE_PATH" \
  REAL_CURL="$REAL_CURL" \
  REAL_MV="$REAL_MV" \
  CURL_LOG="$CASE_CURL_LOG" \
  UV_LOG="$CASE_UV_LOG" \
  BUN_LOG="$CASE_BUN_LOG" \
  GO_LOG="$CASE_GO_LOG" \
  RUST_INIT_LOG="$CASE_RUST_INIT_LOG" \
  RUSTUP_LOG="$CASE_RUSTUP_LOG" \
  CARGO_LOG="$CASE_CARGO_LOG" \
  EXPECTED_UV_CACHE="$CASE_CACHE/uv" \
  EXPECTED_PYTHON_DOWNLOADS="file://$CASE_ROOT/.harness/python-downloads.json" \
  EXPECTED_MANAGED_PYTHON="$CASE_TOOLS/python/3.13.15/runtime/bin/python3.13" \
  EXPECTED_PYTHON_TOOLS="$CASE_ROOT/.harness/python-tools.lock" \
  EXPECTED_BUN_CACHE="$CASE_CACHE/bun" \
  EXPECTED_BUN_PACKAGE="$CASE_ROOT/.harness/bun-tools/package.json" \
  EXPECTED_BUN_LOCK="$CASE_ROOT/.harness/bun-tools/bun.lock" \
  EXPECTED_GOROOT="$CASE_TOOLS/go/1.27.0/go" \
  EXPECTED_GOMODCACHE="$CASE_CACHE/go/mod" \
  EXPECTED_GOCACHE="$CASE_CACHE/go/build" \
  EXPECTED_GO_MOD="$CASE_ROOT/.harness/go-tools/go.mod" \
  EXPECTED_GO_SUM="$CASE_ROOT/.harness/go-tools/go.sum" \
  EXPECTED_RUST_PLATFORM="$CASE_RUST_PLATFORM" \
  EXPECTED_RUST_TARGET="$CASE_RUST_TARGET" \
  EXPECTED_RUST_TOOLCHAIN="1.97.1-$CASE_RUST_TARGET" \
  EXPECTED_RUST_DIST_LOCK="$CASE_ROOT/.harness/rust-dist.lock" \
  EXPECTED_CARGO_MODULES_LOCK="$CASE_ROOT/.harness/cargo-modules.lock" \
  EXPECTED_CARGO_CACHE="$CASE_CACHE/cargo" \
  EXPECTED_MANAGED_RUSTUP_HOME="$CASE_TOOLS/rust/1.97.1/rustup" \
  EXPECTED_MANAGED_RUSTDOC="$CASE_TOOLS/rust/1.97.1/cargo/bin/rustdoc" \
  FAKE_RUSTUP_PROXY_TEMPLATE="$CASE_RUST_ARCHIVES/fake-rustup-proxy" \
  FAKE_UV_RUNTIME_MODE="${FAKE_UV_RUNTIME_MODE:-one}" \
  FAKE_UV_PIP_MODE="${FAKE_UV_PIP_MODE:-ok}" \
  FAKE_UV_OFFLINE_CACHE="${FAKE_UV_OFFLINE_CACHE:-warm}" \
  FAKE_BUN_INSTALL_MODE="${FAKE_BUN_INSTALL_MODE:-ok}" \
  FAKE_BUN_OFFLINE_CACHE="${FAKE_BUN_OFFLINE_CACHE:-warm}" \
  FAKE_BUN_PAYLOAD_MODE="${FAKE_BUN_PAYLOAD_MODE:-present}" \
  FAKE_GO_BUILD_MODE="${FAKE_GO_BUILD_MODE:-ok}" \
  FAKE_GO_OFFLINE_CACHE="${FAKE_GO_OFFLINE_CACHE:-warm}" \
  FAKE_GO_METADATA_MODE="${FAKE_GO_METADATA_MODE:-exact}" \
  FAKE_GO_PAYLOAD_MODE="${FAKE_GO_PAYLOAD_MODE:-present}" \
  FAKE_RUSTUP_INIT_MODE="${FAKE_RUSTUP_INIT_MODE:-ok}" \
  FAKE_RUSTUP_INSTALL_MODE="${FAKE_RUSTUP_INSTALL_MODE:-ok}" \
  FAKE_RUSTUP_PAYLOAD_MODE="${FAKE_RUSTUP_PAYLOAD_MODE:-exact}" \
  FAKE_CARGO_BUILD_MODE="${FAKE_CARGO_BUILD_MODE:-ok}" \
  FAKE_CARGO_OFFLINE_CACHE="${FAKE_CARGO_OFFLINE_CACHE:-warm}" \
  FAKE_CARGO_PAYLOAD_MODE="${FAKE_CARGO_PAYLOAD_MODE:-exact}" \
  HTTPS_ARTIFACT="$CASE_HTTPS_ARTIFACT" \
  HARNESS_WORKSPACE_TESTING=1 \
  HARNESS_WORKSPACE_ROOT="$CASE_ROOT" \
  HARNESS_WORKSPACE_LOCK="$CASE_LOCK" \
  HARNESS_WORKSPACE_TOOLS="$CASE_TOOLS" \
  HARNESS_WORKSPACE_CACHE_ROOT="$CASE_CACHE" \
  OFFLINE="${WORKSPACE_OFFLINE:-0}" \
  "$SCRIPT" "$@"
}

EXEC_INSPECTOR=
POISON_LOG=

prepare_exec_inspector() {
  local base=${CASE_ROOT%/repo}
  EXEC_INSPECTOR=$base/exec-inspector
  POISON_LOG=$base/poison.log
  : >"$POISON_LOG"
  cat >"$EXEC_INSPECTOR" <<'EOF'
#!/bin/sh
set -eu

printf 'PATH=%s\n' "$PATH"
printf 'UV_CACHE_DIR=%s\n' "${UV_CACHE_DIR-<unset>}"
printf 'UV_PYTHON_INSTALL_DIR=%s\n' "${UV_PYTHON_INSTALL_DIR-<unset>}"
printf 'UV_OFFLINE=%s\n' "${UV_OFFLINE-<unset>}"
printf 'GOPROXY=%s\n' "${GOPROXY-<unset>}"
printf 'CARGO_NET_OFFLINE=%s\n' "${CARGO_NET_OFFLINE-<unset>}"
printf 'HARNESS_WORKSPACE_OFFLINE=%s\n' "${HARNESS_WORKSPACE_OFFLINE-<unset>}"
printf 'GOROOT=%s\n' "${GOROOT-<unset>}"
printf 'GOENV=%s\n' "${GOENV-<unset>}"
printf 'GOTOOLCHAIN=%s\n' "${GOTOOLCHAIN-<unset>}"
printf 'GOFLAGS=%s\n' "${GOFLAGS-<unset>}"
printf 'RUSTUP_HOME=%s\n' "${RUSTUP_HOME-<unset>}"
printf 'RUSTUP_TOOLCHAIN=%s\n' "${RUSTUP_TOOLCHAIN-<unset>}"
printf 'RUSTUP_AUTO_INSTALL=%s\n' "${RUSTUP_AUTO_INSTALL-<unset>}"

for executable in "$@"; do
  resolved=$(command -v "$executable") || exit 181
  printf 'COMMAND=%s=%s\n' "$executable" "$resolved"
  case $executable in
    uv|python3.13|lizard|vulture|pip-audit|bun|knip|golangci-lint|govulncheck|gremlins|rustc|cargo|rustfmt|cargo-clippy|cargo-audit|cargo-modules)
      "$executable" --version >/dev/null
      ;;
    go)
      "$executable" version >/dev/null
      ;;
    go-arch-lint)
      "$executable" version >/dev/null
      ;;
    rustup)
      "$executable" component list --installed --toolchain "$RUSTUP_TOOLCHAIN" >/dev/null
      ;;
    cargo-llvm-cov)
      "$executable" llvm-cov --version >/dev/null
      ;;
    *) exit 182 ;;
  esac
done
EOF
  chmod +x "$EXEC_INSPECTOR"
}

poison_exec_commands() {
  local directory=${CASE_PATH%%:*} executable
  : >"$POISON_LOG"
  for executable in "$@"; do
    cat >"$directory/$executable" <<EOF
#!/bin/sh
printf '%s\n' '$executable' >>'$POISON_LOG'
exit 183
EOF
    chmod +x "$directory/$executable"
  done
}

capture_exec() {
  local offline=$1 profile=$2 status
  shift 2
  export UV_OFFLINE=poisoned
  export GOPROXY=poisoned
  export CARGO_NET_OFFLINE=poisoned
  export HARNESS_WORKSPACE_OFFLINE=poisoned
  export GOROOT=/poisoned/go
  export GOENV=/poisoned/goenv
  export GOTOOLCHAIN=poisoned
  export GOFLAGS=poisoned
  export RUSTUP_HOME=/poisoned/rustup
  export RUSTUP_TOOLCHAIN=poisoned
  export RUSTUP_AUTO_INSTALL=poisoned
  if WORKSPACE_OFFLINE=$offline workspace exec "$profile" -- "$EXEC_INSPECTOR" "$@" \
    >"$TMP/output" 2>&1; then
    status=0
  else
    status=$?
  fi
  unset UV_OFFLINE GOPROXY CARGO_NET_OFFLINE HARNESS_WORKSPACE_OFFLINE
  unset GOROOT GOENV GOTOOLCHAIN GOFLAGS
  unset RUSTUP_HOME RUSTUP_TOOLCHAIN RUSTUP_AUTO_INSTALL
  return "$status"
}

assert_exec_path_set() {
  local name=$1 output=$2 path managed_path expected actual_path found
  local expected_count actual_count
  shift 2
  path=$(sed -n 's/^PATH=//p' "$output")
  case $path in
    *:"$CASE_PATH") managed_path=${path%:"$CASE_PATH"} ;;
    *) fail "$name did not retain the fixture bootstrap PATH as its suffix" ;;
  esac
  expected_count=$#
  actual_count=0
  local IFS=:
  local actual_paths=()
  read -r -a actual_paths <<<"$managed_path"
  actual_count=${#actual_paths[@]}
  [ "$actual_count" -eq "$expected_count" ] || \
    fail "$name managed PATH count differs: $managed_path"
  for expected in "$@"; do
    found=0
    for actual_path in "${actual_paths[@]}"; do
      [ "$actual_path" != "$expected" ] || found=$((found + 1))
    done
    [ "$found" -eq 1 ] || fail "$name managed PATH lacks one exact $expected entry"
  done
}

assert_exec_command() {
  local output=$1 executable=$2 expected=$3
  grep -F "COMMAND=$executable=$expected" "$output" >/dev/null || \
    fail "$executable did not resolve to $expected"
}

assert_exec_env() {
  local output=$1 name=$2 expected=$3
  grep -F "$name=$expected" "$output" >/dev/null || \
    fail "$name did not resolve to $expected"
}

assert_path_excludes() {
  local output=$1 fragment=$2 path
  path=$(sed -n 's/^PATH=//p' "$output")
  case ${path%:"$CASE_PATH"} in
    *"$fragment"*) fail "unselected profile path leaked into managed PATH: $fragment" ;;
  esac
}

assert_poison_unused() {
  [ ! -s "$POISON_LOG" ] || fail "ambient poison command executed: $(<"$POISON_LOG")"
}

write_platform_shims() {
  local directory=$1
  mkdir -p "$directory"
  cat >"$directory/uname" <<'EOF'
#!/bin/sh
case ${1:-} in
  -s) printf '%s\n' "$FAKE_KERNEL" ;;
  -m) printf '%s\n' "$FAKE_MACHINE" ;;
  *) exit 2 ;;
esac
EOF
  cat >"$directory/getconf" <<'EOF'
#!/bin/sh
[ "${FAKE_LIBC:-glibc}" = glibc ] || exit 1
printf '%s\n' 'glibc 2.39'
EOF
  cat >"$directory/ldd" <<'EOF'
#!/bin/sh
if [ "${FAKE_LIBC:-glibc}" = musl ]; then
  printf '%s\n' 'musl libc'
else
  printf '%s\n' 'ldd (GNU libc) 2.39'
fi
EOF
  chmod +x "$directory/uname" "$directory/getconf" "$directory/ldd"
}

platform_bin=$TMP/platform-bin
write_platform_shims "$platform_bin"
for mapping in 'Darwin x86_64 darwin-x86_64' 'Darwin arm64 darwin-arm64' \
  'Linux x86_64 linux-x86_64' 'Linux aarch64 linux-arm64'; do
  set -- $mapping
  output=$(FAKE_KERNEL=$1 FAKE_MACHINE=$2 FAKE_LIBC=glibc PATH="$platform_bin:$ORIGINAL_PATH" "$SCRIPT" platform)
  [ "$output" = "$3" ] || fail "platform mapping $1/$2"
done
ok "all supported OS and architecture mappings resolve"

expect_fail "Windows is rejected" "unsupported operating system" \
  env FAKE_KERNEL=MINGW64_NT FAKE_MACHINE=x86_64 PATH="$platform_bin:$ORIGINAL_PATH" "$SCRIPT" platform
expect_fail "unsupported CPUs are rejected" "unsupported CPU 'riscv64'" \
  env FAKE_KERNEL=Linux FAKE_MACHINE=riscv64 PATH="$platform_bin:$ORIGINAL_PATH" "$SCRIPT" platform
expect_fail "musl Linux is rejected" "unsupported libc 'musl'" \
  env FAKE_KERNEL=Linux FAKE_MACHINE=x86_64 FAKE_LIBC=musl PATH="$platform_bin:$ORIGINAL_PATH" "$SCRIPT" platform

new_fixture prerequisites
empty_path=$TMP/empty-path
mkdir -p "$empty_path"
if HOME="$CASE_HOME" PATH="$empty_path" HARNESS_WORKSPACE_TESTING=1 \
  HARNESS_WORKSPACE_ROOT="$CASE_ROOT" HARNESS_WORKSPACE_LOCK="$CASE_LOCK" \
  HARNESS_WORKSPACE_TOOLS="$CASE_TOOLS" HARNESS_WORKSPACE_CACHE_ROOT="$CASE_CACHE" \
  /bin/bash "$SCRIPT" preflight >"$TMP/output" 2>&1; then
  fail "aggregate prerequisites (unexpected success)"
fi
for missing in make bash git curl tar unzip cmp diff awk mktemp mkdir cp chmod mv rm \
  'sha256sum or shasum'; do
  grep -F "$missing" "$TMP/output" >/dev/null || fail "aggregate prerequisites (missing $missing)"
done
ok "preflight aggregates missing prerequisites"

new_fixture unzip-capability
cat >"${CASE_PATH%%:*}/unzip" <<'EOF'
#!/bin/sh
exit 2
EOF
chmod +x "${CASE_PATH%%:*}/unzip"
expect_fail "preflight rejects unzip without listing support" \
  "unzip must provide Info-ZIP -Z listing mode" workspace preflight

new_fixture convergence
printf '%s\n' 'preserve me' >"$CASE_ROOT/untracked.txt"
expect_ok "clean preflight permits untracked files" workspace preflight
expect_ok "fresh online install converges" workspace install
[ -f "$CASE_ROOT/untracked.txt" ] || fail "fresh online install preserves untracked files"
[ -x "$CASE_TOOLS/probe/1.0.0/bin/probe" ] || fail "fresh online install publishes executable"
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = 1 ] || fail "fresh online install downloads once"
ok "fresh install preserves untracked files and publishes one verified artifact"

first_receipt=$(sha_of "$CASE_TOOLS/probe/1.0.0/.harness-workspace-receipt")
expect_ok "second online run reuses exact tool" workspace install
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = 1 ] || fail "second online run downloaded"
WORKSPACE_OFFLINE=1 expect_ok "warm offline run reuses exact tool" workspace install
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = 1 ] || fail "offline run used curl"
[ "$(sha_of "$CASE_TOOLS/probe/1.0.0/.harness-workspace-receipt")" = "$first_receipt" ] || fail "receipt changed on rerun"
ok "online rerun and warm offline run are byte-stable and download-free"

metadata_artifact=${CASE_ARTIFACT}.mirror
cp "$CASE_ARTIFACT" "$metadata_artifact"
write_lock "$CASE_LOCK" "$metadata_artifact" "$(sha_of "$metadata_artifact")"
git -C "$CASE_ROOT" add .harness/workspace.lock
git -C "$CASE_ROOT" commit -qm metadata-change
expect_ok "artifact metadata change invalidates the receipt" workspace install
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = 2 ] || fail "metadata change reused a stale receipt"

new_fixture secure-https
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" { $4 = "https://example.test/probe" } { print }' \
  "$CASE_LOCK" >"$CASE_LOCK.new"
mv "$CASE_LOCK.new" "$CASE_LOCK"
cat >"${CASE_PATH%%:*}/curl" <<'EOF'
#!/bin/sh
proto=0
proto_redir=0
tls=0
destination=
while [ "$#" -gt 0 ]; do
  case $1 in
    --proto) [ "${2:-}" = '=https' ] || exit 81; proto=1; shift 2 ;;
    --proto-redir) [ "${2:-}" = '=https' ] || exit 82; proto_redir=1; shift 2 ;;
    --tlsv1.2) tls=1; shift ;;
    --output) destination=${2:-}; shift 2 ;;
    *) shift ;;
  esac
done
[ "$proto" -eq 1 ] && [ "$proto_redir" -eq 1 ] && [ "$tls" -eq 1 ] && [ -n "$destination" ] || exit 83
cp "$HTTPS_ARTIFACT" "$destination"
EOF
chmod +x "${CASE_PATH%%:*}/curl"
git -C "$CASE_ROOT" add .harness/workspace.lock
git -C "$CASE_ROOT" commit -qm secure-https
expect_ok "test mode keeps HTTPS protocol and TLS restrictions" workspace install

for archive_format in tar.gz tgz zip; do
  new_fixture "archive-${archive_format//./-}"
  fixture_base=${CASE_ROOT%/repo}
  archive_source=$fixture_base/archive-source
  archive_path=$fixture_base/releases/probe.$archive_format
  mkdir -p "$archive_source/bundle"
  cp "$CASE_ARTIFACT" "$archive_source/bundle/probe"
  case $archive_format in
    tar.gz|tgz) tar -czf "$archive_path" -C "$archive_source" bundle ;;
    zip)
      command -v zip >/dev/null 2>&1 || fail "zip test prerequisite is missing"
      (cd "$archive_source" && zip -q -r "$archive_path" bundle)
      ;;
  esac
  write_lock "$CASE_LOCK" "$archive_path" "$(sha_of "$archive_path")" "$archive_format" bundle/probe
  git -C "$CASE_ROOT" add .harness/workspace.lock
  git -C "$CASE_ROOT" commit -qm "$archive_format fixture"
  expect_ok "$archive_format payload extracts and verifies" workspace install
  [ "$($CASE_TOOLS/probe/1.0.0/bin/probe --version)" = 'probe 1.0.0' ] || \
    fail "$archive_format payload did not execute"
done

new_fixture archive-symlink
fixture_base=${CASE_ROOT%/repo}
archive_source=$fixture_base/archive-source
archive_path=$fixture_base/releases/probe-with-symlink.tar.gz
mkdir -p "$archive_source/bundle"
cp "$CASE_ARTIFACT" "$archive_source/bundle/probe"
ln -s ../../outside "$archive_source/bundle/escape"
tar -czf "$archive_path" -C "$archive_source" bundle
write_lock "$CASE_LOCK" "$archive_path" "$(sha_of "$archive_path")" tar.gz bundle/probe
git -C "$CASE_ROOT" add .harness/workspace.lock
git -C "$CASE_ROOT" commit -qm unsafe-archive
expect_fail "archive symlinks are rejected before extraction" "unsafe or unsupported archive contents" workspace install
[ ! -e "$CASE_TOOLS/probe/1.0.0" ] || fail "unsafe archive published a tool"

new_fixture cold-offline
WORKSPACE_OFFLINE=1 expect_fail "cold offline fails without download" "offline cache miss" workspace install
[ ! -e "$CASE_CURL_LOG" ] || fail "cold offline invoked curl"
[ ! -e "$CASE_ROOT/.git/hooks/pre-commit" ] || fail "cold offline modified hooks"
[ ! -e "$CASE_HOME/.claude/skills/harness" ] || fail "cold offline modified skills"

new_fixture checksum
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" { $5 = "0000000000000000000000000000000000000000000000000000000000000000" } { print }' \
  "$CASE_LOCK" >"$CASE_LOCK.new"
mv "$CASE_LOCK.new" "$CASE_LOCK"
git -C "$CASE_ROOT" add .harness/workspace.lock
git -C "$CASE_ROOT" commit -qm bad-checksum
expect_fail "checksum mismatch blocks publication" "checksum mismatch" workspace install
[ ! -e "$CASE_TOOLS/probe/1.0.0" ] || fail "checksum mismatch published a tool"
if compgen -G "$CASE_TOOLS/probe/.1.0.0.tmp.*" >/dev/null; then
  fail "checksum failure left a temporary directory"
fi

new_fixture recovery
expect_ok "recovery fixture installs" workspace install
printf '%s\n' 'corrupt' >"$CASE_TOOLS/probe/1.0.0/bin/probe"
chmod +x "$CASE_TOOLS/probe/1.0.0/bin/probe"
expect_ok "corrupt installation is atomically replaced" workspace install
[ "$($CASE_TOOLS/probe/1.0.0/bin/probe --version)" = 'probe 1.0.0' ] || fail "corrupt installation remained"

old_binary_sha=$(sha_of "$CASE_TOOLS/probe/1.0.0/bin/probe")
awk 'BEGIN { FS = OFS = "\t" } $1 == "artifact" { $5 = "1111111111111111111111111111111111111111111111111111111111111111" } { print }' \
  "$CASE_LOCK" >"$CASE_LOCK.new"
mv "$CASE_LOCK.new" "$CASE_LOCK"
git -C "$CASE_ROOT" add .harness/workspace.lock
git -C "$CASE_ROOT" commit -qm changed-checksum
expect_fail "bad replacement leaves prior installation intact" "checksum mismatch" workspace install
[ "$(sha_of "$CASE_TOOLS/probe/1.0.0/bin/probe")" = "$old_binary_sha" ] || fail "bad replacement damaged prior tool"

new_fixture publication-rollback
expect_ok "publication rollback fixture installs" workspace install
printf '%s\n' stale >"$CASE_TOOLS/probe/1.0.0/.harness-workspace-receipt"
old_binary_sha=$(sha_of "$CASE_TOOLS/probe/1.0.0/bin/probe")
old_receipt_sha=$(sha_of "$CASE_TOOLS/probe/1.0.0/.harness-workspace-receipt")
cat >"${CASE_PATH%%:*}/mv" <<'EOF'
#!/bin/sh
case ${1:-} in
  */stage)
    printf '%s\n' 'injected publication failure' >&2
    exit 75
    ;;
esac
exec "$REAL_MV" "$@"
EOF
chmod +x "${CASE_PATH%%:*}/mv"
expect_fail "failed publication restores the prior installation" "injected publication failure" workspace install
[ "$(sha_of "$CASE_TOOLS/probe/1.0.0/bin/probe")" = "$old_binary_sha" ] || \
  fail "failed publication changed the prior binary"
[ "$(sha_of "$CASE_TOOLS/probe/1.0.0/.harness-workspace-receipt")" = "$old_receipt_sha" ] || \
  fail "failed publication changed the prior receipt"
if compgen -G "$CASE_TOOLS/probe/.1.0.0.tmp.*" >/dev/null || \
  compgen -G "$CASE_TOOLS/probe/.1.0.0.old.*" >/dev/null; then
  fail "failed publication left temporary or backup state"
fi

new_fixture dirty
printf '%s\n' '# dirty' >>"$CASE_ROOT/Makefile"
expect_fail "dirty tracked worktree is rejected" "tracked worktree changes" workspace preflight
git -C "$CASE_ROOT" restore Makefile
printf '%s\n' '# staged' >>"$CASE_ROOT/Makefile"
git -C "$CASE_ROOT" add Makefile
expect_fail "dirty index is rejected" "staged index changes" workspace preflight

new_fixture managed-env
expect_ok "managed environment fixture installs" workspace install
cat >"${CASE_PATH%%:*}/probe" <<'EOF'
#!/bin/sh
printf '%s\n' 'ambient probe must not run'
exit 99
EOF
chmod +x "${CASE_PATH%%:*}/probe"
WORKSPACE_OFFLINE=1 workspace exec common -- probe env >"$TMP/output" 2>&1 || fail "managed exec"
grep -F "PATH=$CASE_TOOLS/probe/1.0.0/bin:" "$TMP/output" >/dev/null || fail "managed PATH precedence"
grep -F "UV_CACHE_DIR=$CASE_CACHE/uv" "$TMP/output" >/dev/null || fail "managed uv cache"
grep -F "UV_PYTHON_INSTALL_DIR=$CASE_TOOLS/python/3.13.15" "$TMP/output" >/dev/null || fail "managed Python path"
grep -F 'UV_OFFLINE=1' "$TMP/output" >/dev/null || fail "offline uv environment"
grep -F 'GOPROXY=off' "$TMP/output" >/dev/null || fail "offline Go environment"
grep -F 'CARGO_NET_OFFLINE=true' "$TMP/output" >/dev/null || fail "offline Cargo environment"
ok "exec ignores ambient tools and exports managed caches/offline policy"

new_python_fixture python-convergence
cat >"${CASE_PATH%%:*}/uv" <<'EOF'
#!/bin/sh
printf '%s\n' 'ambient uv must not run' >&2
exit 99
EOF
chmod +x "${CASE_PATH%%:*}/uv"
expect_ok "fresh managed Python install converges" workspace install common
python_runtime=$CASE_TOOLS/python/3.13.15/runtime/bin/python3.13
[ -x "$python_runtime" ] || fail "managed Python runtime is missing"
[ "$($python_runtime --version)" = 'Python 3.13.15' ] || fail "managed Python version differs"
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 1 ] || fail "managed uv install call count differs"
grep -F "$CASE_TOOLS/uv/0.12.5/bin/uv" "$CASE_UV_LOG" >/dev/null || fail "ambient uv was selected"
grep -F "$CASE_CACHE/uv" "$CASE_UV_LOG" >/dev/null || fail "managed uv cache was not selected"
grep -F -- '--no-bin --managed-python --no-config' "$CASE_UV_LOG" >/dev/null || \
  fail "managed uv safety flags are missing"
grep -F -- "--python-downloads-json-url file://$CASE_ROOT/.harness/python-downloads.json" \
  "$CASE_UV_LOG" >/dev/null || fail "checked Python downloads input was not selected"
ok "managed uv installs the exact normalized Python runtime"

python_receipt_sha=$(sha_of "$CASE_TOOLS/python/3.13.15/.harness-workspace-receipt")
expect_ok "second managed Python install reuses exact runtime" workspace install common
WORKSPACE_OFFLINE=1 expect_ok "warm offline managed Python install reuses exact runtime" workspace install common
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 1 ] || fail "Python reuse invoked uv install"
[ "$(sha_of "$CASE_TOOLS/python/3.13.15/.harness-workspace-receipt")" = "$python_receipt_sha" ] || \
  fail "Python receipt changed during reuse"
ok "managed Python reuse is byte-stable and download-free"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "Python corrupt"' >"$python_runtime"
chmod +x "$python_runtime"
corrupt_python_sha=$(sha_of "$python_runtime")
WORKSPACE_OFFLINE=1 expect_fail "offline Python repair fails without modifying runtime" \
  "offline cache miss for python 3.13.15" workspace install common
[ "$(sha_of "$python_runtime")" = "$corrupt_python_sha" ] || fail "offline failure changed corrupt Python"
expect_ok "online Python repair atomically replaces corrupt runtime" workspace install common
[ "$($python_runtime --version)" = 'Python 3.13.15' ] || fail "online repair did not restore Python"
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 2 ] || fail "online repair did not invoke managed uv once"
ok "managed Python repair preserves the old runtime until replacement verifies"

new_python_fixture python-no-runtime
FAKE_UV_RUNTIME_MODE=zero expect_fail "missing uv Python payload blocks publication" \
  "produced 0 runtimes; expected exactly one" workspace install common
[ ! -e "$CASE_TOOLS/python/3.13.15" ] || fail "missing Python payload was published"

new_python_fixture python-two-runtimes
FAKE_UV_RUNTIME_MODE=two expect_fail "ambiguous uv Python payload blocks publication" \
  "produced 2 runtimes; expected exactly one" workspace install common
[ ! -e "$CASE_TOOLS/python/3.13.15" ] || fail "ambiguous Python payload was published"

new_python_cli_fixture python-cli-profiles
expect_ok "common profile installs the managed Lizard CLI" workspace install common
lizard_bin=$CASE_TOOLS/lizard/1.22.2/bin/lizard
[ "$($lizard_bin --version)" = 1.22.2 ] || fail "managed Lizard probe differs"
[ ! -e "$CASE_TOOLS/vulture/2.16" ] || fail "common profile installed Vulture"
[ ! -e "$CASE_TOOLS/pip-audit/2.10.1" ] || fail "common profile installed pip-audit"
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 3 ] || fail "common Python CLI call count differs"
grep -F "pip install --no-config --no-python-downloads" "$CASE_UV_LOG" >/dev/null || \
  fail "managed uv pip was not selected"
grep -F -- "--require-hashes --no-build --constraint $CASE_ROOT/.harness/python-tools.lock lizard==1.22.2" \
  "$CASE_UV_LOG" >/dev/null || fail "Lizard did not use the exact hash lock"
ok "common profile remains isolated and hash locked"

expect_ok "python profile installs Vulture and pip-audit" workspace install python
vulture_bin=$CASE_TOOLS/vulture/2.16/bin/vulture
pip_audit_bin=$CASE_TOOLS/pip-audit/2.10.1/bin/pip-audit
[ "$($vulture_bin --version)" = 'vulture 2.16' ] || fail "managed Vulture probe differs"
[ "$($pip_audit_bin --version)" = 'pip-audit 2.10.1' ] || fail "managed pip-audit probe differs"
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 7 ] || fail "Python profile CLI call count differs"
ok "Python profile adds only its exact managed CLIs"

prepare_exec_inspector
poison_exec_commands uv python3.13 lizard vulture pip-audit
if ! capture_exec 0 common uv python3.13 lizard; then
  fail "online common exec did not use its sealed managed environment"
fi
assert_exec_path_set "common profile" "$TMP/output" \
  "$CASE_TOOLS/uv/0.12.5/bin" \
  "$CASE_TOOLS/python/3.13.15/runtime/bin" \
  "$CASE_TOOLS/lizard/1.22.2/bin"
assert_exec_command "$TMP/output" uv "$CASE_TOOLS/uv/0.12.5/bin/uv"
assert_exec_command "$TMP/output" python3.13 \
  "$CASE_TOOLS/python/3.13.15/runtime/bin/python3.13"
assert_exec_command "$TMP/output" lizard "$CASE_TOOLS/lizard/1.22.2/bin/lizard"
assert_exec_env "$TMP/output" UV_CACHE_DIR "$CASE_CACHE/uv"
assert_exec_env "$TMP/output" UV_PYTHON_INSTALL_DIR "$CASE_TOOLS/python/3.13.15"
assert_exec_env "$TMP/output" UV_OFFLINE 0
assert_exec_env "$TMP/output" GOPROXY 'https://proxy.golang.org,direct'
assert_exec_env "$TMP/output" CARGO_NET_OFFLINE false
assert_exec_env "$TMP/output" HARNESS_WORKSPACE_OFFLINE 0
assert_path_excludes "$TMP/output" '/vulture/'
assert_path_excludes "$TMP/output" '/pip-audit/'
assert_poison_unused
ok "common exec exposes only exact managed Python and Lizard commands online"

if ! capture_exec 1 python uv python3.13 lizard vulture pip-audit; then
  fail "offline Python exec did not use its sealed managed environment"
fi
assert_exec_path_set "Python profile" "$TMP/output" \
  "$CASE_TOOLS/uv/0.12.5/bin" \
  "$CASE_TOOLS/python/3.13.15/runtime/bin" \
  "$CASE_TOOLS/lizard/1.22.2/bin" \
  "$CASE_TOOLS/vulture/2.16/bin" \
  "$CASE_TOOLS/pip-audit/2.10.1/bin"
assert_exec_command "$TMP/output" vulture "$CASE_TOOLS/vulture/2.16/bin/vulture"
assert_exec_command "$TMP/output" pip-audit "$CASE_TOOLS/pip-audit/2.10.1/bin/pip-audit"
assert_exec_env "$TMP/output" UV_OFFLINE 1
assert_exec_env "$TMP/output" GOPROXY off
assert_exec_env "$TMP/output" CARGO_NET_OFFLINE true
assert_exec_env "$TMP/output" HARNESS_WORKSPACE_OFFLINE 1
assert_poison_unused
ok "Python exec composes exact profile CLIs and seals offline policy"

python_cli_receipts=$(for receipt in \
  "$CASE_TOOLS/lizard/1.22.2/.harness-workspace-receipt" \
  "$CASE_TOOLS/vulture/2.16/.harness-workspace-receipt" \
  "$CASE_TOOLS/pip-audit/2.10.1/.harness-workspace-receipt"; do sha_of "$receipt"; done)
expect_ok "second Python CLI install reuses exact environments" workspace install python
WORKSPACE_OFFLINE=1 expect_ok "warm offline Python CLI install reuses exact environments" workspace install python
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 7 ] || fail "Python CLI reuse invoked uv"
current_python_cli_receipts=$(for receipt in \
  "$CASE_TOOLS/lizard/1.22.2/.harness-workspace-receipt" \
  "$CASE_TOOLS/vulture/2.16/.harness-workspace-receipt" \
  "$CASE_TOOLS/pip-audit/2.10.1/.harness-workspace-receipt"; do sha_of "$receipt"; done)
[ "$current_python_cli_receipts" = "$python_cli_receipts" ] || fail "Python CLI receipts changed on reuse"
ok "Python CLI reuse is byte-stable and download-free"

mv "$CASE_TOOLS/lizard/1.22.2" "$TMP/lizard-saved"
WORKSPACE_OFFLINE=1 expect_ok "warm cache rebuilds a missing Python CLI offline" workspace install common
[ "$($lizard_bin --version)" = 1.22.2 ] || fail "offline Lizard rebuild differs"
[ "$(wc -l <"$CASE_UV_LOG" | tr -d ' ')" = 9 ] || fail "offline Lizard rebuild call count differs"
tail -n 2 "$CASE_UV_LOG" | grep -F -- '--offline' >/dev/null || fail "offline flags were not forwarded"

mv "$CASE_TOOLS/vulture/2.16" "$TMP/vulture-saved"
FAKE_UV_OFFLINE_CACHE=cold WORKSPACE_OFFLINE=1 expect_fail \
  "cold Python package cache blocks offline publication" "offline cache miss for vulture 2.16" \
  workspace install python
[ ! -e "$CASE_TOOLS/vulture/2.16" ] || fail "cold offline Vulture was published"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "corrupt lizard"' >"$lizard_bin"
chmod +x "$lizard_bin"
corrupt_lizard_sha=$(sha_of "$lizard_bin")
FAKE_UV_PIP_MODE=fail expect_fail "failed Python CLI repair preserves prior installation" \
  "failed to install managed Python CLI lizard 1.22.2" workspace install common
[ "$(sha_of "$lizard_bin")" = "$corrupt_lizard_sha" ] || fail "failed Lizard repair changed prior bytes"
expect_ok "online Python CLI repair atomically restores exact command" workspace install common
[ "$($lizard_bin --version)" = 1.22.2 ] || fail "online Lizard repair differs"

new_bun_fixture knip-convergence
expect_ok "Bun profile installs managed Knip" workspace install bun
knip_bin=$CASE_TOOLS/knip/5.88.1/bin/knip
[ "$($knip_bin --version)" = 5.88.1 ] || fail "managed Knip probe differs"
[ ! -e "$CASE_TOOLS/vulture/2.16" ] || fail "Bun profile installed Vulture"
[ ! -e "$CASE_TOOLS/pip-audit/2.10.1" ] || fail "Bun profile installed pip-audit"
[ "$(wc -l <"$CASE_BUN_LOG" | tr -d ' ')" = 1 ] || fail "Knip install call count differs"
grep -F "$CASE_TOOLS/bun/1.3.14/bin/bun" "$CASE_BUN_LOG" >/dev/null || fail "ambient Bun was selected"
grep -F "$CASE_CACHE/bun" "$CASE_BUN_LOG" >/dev/null || fail "managed Bun cache was not selected"
grep -F -- "--frozen-lockfile --ignore-scripts --backend copyfile --linker hoisted" \
  "$CASE_BUN_LOG" >/dev/null || fail "frozen Knip install flags are missing"
cmp -s "$CASE_TOOLS/knip/5.88.1/runtime/package.json" "$CASE_ROOT/.harness/bun-tools/package.json" || \
  fail "Knip package input changed"
cmp -s "$CASE_TOOLS/knip/5.88.1/runtime/bun.lock" "$CASE_ROOT/.harness/bun-tools/bun.lock" || \
  fail "Knip lock input changed"
ok "Knip install uses the exact managed Bun and frozen inputs"

prepare_exec_inspector
poison_exec_commands uv python3.13 lizard bun knip vulture pip-audit
if ! capture_exec 0 bun uv python3.13 lizard bun knip; then
  fail "Bun exec did not use its sealed managed environment"
fi
assert_exec_path_set "Bun profile" "$TMP/output" \
  "$CASE_TOOLS/uv/0.12.5/bin" \
  "$CASE_TOOLS/bun/1.3.14/bin" \
  "$CASE_TOOLS/python/3.13.15/runtime/bin" \
  "$CASE_TOOLS/lizard/1.22.2/bin" \
  "$CASE_TOOLS/knip/5.88.1/bin"
assert_exec_command "$TMP/output" bun "$CASE_TOOLS/bun/1.3.14/bin/bun"
assert_exec_command "$TMP/output" knip "$CASE_TOOLS/knip/5.88.1/bin/knip"
assert_path_excludes "$TMP/output" '/vulture/'
assert_path_excludes "$TMP/output" '/pip-audit/'
assert_poison_unused
ok "Bun exec resolves only exact managed Bun and Knip profile commands"

knip_receipt_sha=$(sha_of "$CASE_TOOLS/knip/5.88.1/.harness-workspace-receipt")
expect_ok "second Knip install reuses exact runtime" workspace install bun
WORKSPACE_OFFLINE=1 expect_ok "warm offline Knip install reuses exact runtime" workspace install bun
[ "$(wc -l <"$CASE_BUN_LOG" | tr -d ' ')" = 1 ] || fail "Knip reuse invoked Bun install"
[ "$(sha_of "$CASE_TOOLS/knip/5.88.1/.harness-workspace-receipt")" = "$knip_receipt_sha" ] || \
  fail "Knip receipt changed on reuse"
ok "Knip reuse is byte-stable and download-free"

mv "$CASE_TOOLS/knip/5.88.1" "$TMP/knip-saved"
WORKSPACE_OFFLINE=1 expect_ok "warm Bun cache rebuilds missing Knip offline" workspace install bun
[ "$($knip_bin --version)" = 5.88.1 ] || fail "offline Knip rebuild differs"
[ "$(wc -l <"$CASE_BUN_LOG" | tr -d ' ')" = 2 ] || fail "offline Knip rebuild call count differs"
tail -n 1 "$CASE_BUN_LOG" | grep -F -- 'install --offline' >/dev/null || \
  fail "Knip offline flag was not forwarded"

mv "$CASE_TOOLS/knip/5.88.1" "$TMP/knip-saved-2"
FAKE_BUN_OFFLINE_CACHE=cold WORKSPACE_OFFLINE=1 expect_fail \
  "cold Bun cache blocks offline Knip publication" "offline cache miss for knip 5.88.1" \
  workspace install bun
[ ! -e "$CASE_TOOLS/knip/5.88.1" ] || fail "cold offline Knip was published"
mv "$TMP/knip-saved-2" "$CASE_TOOLS/knip/5.88.1"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "corrupt knip"' >"$knip_bin"
chmod +x "$knip_bin"
corrupt_knip_sha=$(sha_of "$knip_bin")
FAKE_BUN_INSTALL_MODE=fail expect_fail "failed Knip repair preserves prior installation" \
  "failed to install managed Knip 5.88.1" workspace install bun
[ "$(sha_of "$knip_bin")" = "$corrupt_knip_sha" ] || fail "failed Knip repair changed prior bytes"
expect_ok "online Knip repair atomically restores exact command" workspace install bun
[ "$($knip_bin --version)" = 5.88.1 ] || fail "online Knip repair differs"

new_bun_fixture knip-missing-payload
FAKE_BUN_PAYLOAD_MODE=missing expect_fail "missing Knip payload blocks publication" \
  "managed Knip failed verification" workspace install bun
[ ! -e "$CASE_TOOLS/knip/5.88.1" ] || fail "missing Knip payload was published"

new_go_fixture go-analyzer-convergence
expect_ok "Go profile installs all managed analyzers" workspace install go
govuln_bin=$CASE_TOOLS/govulncheck/1.1.4/bin/govulncheck
go_arch_bin=$CASE_TOOLS/go-arch-lint/1.15.0/bin/go-arch-lint
gremlins_bin=$CASE_TOOLS/gremlins/0.5.0/bin/gremlins
"$govuln_bin" --version | grep -F 'Scanner: govulncheck@v1.1.4' >/dev/null || fail "govulncheck probe differs"
"$go_arch_bin" version | grep -F 'Linter version:' >/dev/null || fail "go-arch-lint probe differs"
"$gremlins_bin" --version | grep -F 'gremlins version dev' >/dev/null || fail "Gremlins probe differs"
[ ! -e "$CASE_TOOLS/vulture/2.16" ] || fail "Go profile installed Vulture"
[ "$(wc -l <"$CASE_GO_LOG" | tr -d ' ')" = 3 ] || fail "Go analyzer build call count differs"
grep -F "$CASE_TOOLS/go/1.27.0/go/bin/go" "$CASE_GO_LOG" >/dev/null || fail "ambient Go was selected"
grep -F "$CASE_CACHE/go/mod" "$CASE_GO_LOG" >/dev/null || fail "managed module cache was not selected"
grep -F "$CASE_CACHE/go/build" "$CASE_GO_LOG" >/dev/null || fail "managed build cache was not selected"
grep -F $'off/off/local\t0\thttps://proxy.golang.org,direct\tinstall -mod=readonly' \
  "$CASE_GO_LOG" >/dev/null || fail "hermetic Go build environment differs"
if grep -F '@v' "$CASE_GO_LOG" >/dev/null; then
  fail "Go install bypassed the readonly tools module with an @version package"
fi
grep -F $'module\tgolang.org/x/vuln/cmd/govulncheck\tgolang.org/x/vuln\tv1.1.4\th1:Ju8QsuyhX3Hk8ma3CesTbO8vfJD9EvUBgHvkxHBzj0I=' \
  "$CASE_TOOLS/govulncheck/1.1.4/.harness-workspace-receipt" >/dev/null || \
  fail "govulncheck receipt lacks authoritative module identity"
ok "Go analyzers use managed Go, readonly inputs, and exact module metadata"

prepare_exec_inspector
poison_exec_commands uv python3.13 lizard go golangci-lint govulncheck \
  go-arch-lint gremlins vulture pip-audit
if ! capture_exec 0 go uv python3.13 lizard go golangci-lint govulncheck \
  go-arch-lint gremlins; then
  fail "Go exec did not use its sealed managed environment"
fi
assert_exec_path_set "Go profile" "$TMP/output" \
  "$CASE_TOOLS/uv/0.12.5/bin" \
  "$CASE_TOOLS/go/1.27.0/go/bin" \
  "$CASE_TOOLS/golangci-lint/2.12.2/bin" \
  "$CASE_TOOLS/python/3.13.15/runtime/bin" \
  "$CASE_TOOLS/lizard/1.22.2/bin" \
  "$CASE_TOOLS/govulncheck/1.1.4/bin" \
  "$CASE_TOOLS/go-arch-lint/1.15.0/bin" \
  "$CASE_TOOLS/gremlins/0.5.0/bin"
assert_exec_command "$TMP/output" go "$CASE_TOOLS/go/1.27.0/go/bin/go"
assert_exec_command "$TMP/output" golangci-lint \
  "$CASE_TOOLS/golangci-lint/2.12.2/bin/golangci-lint"
assert_exec_command "$TMP/output" govulncheck "$CASE_TOOLS/govulncheck/1.1.4/bin/govulncheck"
assert_exec_command "$TMP/output" go-arch-lint \
  "$CASE_TOOLS/go-arch-lint/1.15.0/bin/go-arch-lint"
assert_exec_command "$TMP/output" gremlins "$CASE_TOOLS/gremlins/0.5.0/bin/gremlins"
assert_exec_env "$TMP/output" GOROOT "$CASE_TOOLS/go/1.27.0/go"
assert_exec_env "$TMP/output" GOENV off
assert_exec_env "$TMP/output" GOTOOLCHAIN local
assert_exec_env "$TMP/output" GOFLAGS ''
assert_exec_env "$TMP/output" GOPROXY 'https://proxy.golang.org,direct'
assert_path_excludes "$TMP/output" '/vulture/'
assert_path_excludes "$TMP/output" '/pip-audit/'
assert_poison_unused
ok "Go exec resolves exact managed Go tools and seals Go environment policy"

go_receipts=$(for receipt in \
  "$CASE_TOOLS/govulncheck/1.1.4/.harness-workspace-receipt" \
  "$CASE_TOOLS/go-arch-lint/1.15.0/.harness-workspace-receipt" \
  "$CASE_TOOLS/gremlins/0.5.0/.harness-workspace-receipt"; do sha_of "$receipt"; done)
expect_ok "second Go analyzer install reuses exact binaries" workspace install go
WORKSPACE_OFFLINE=1 expect_ok "warm offline Go analyzer install reuses exact binaries" workspace install go
[ "$(wc -l <"$CASE_GO_LOG" | tr -d ' ')" = 3 ] || fail "Go analyzer reuse invoked builds"
current_go_receipts=$(for receipt in \
  "$CASE_TOOLS/govulncheck/1.1.4/.harness-workspace-receipt" \
  "$CASE_TOOLS/go-arch-lint/1.15.0/.harness-workspace-receipt" \
  "$CASE_TOOLS/gremlins/0.5.0/.harness-workspace-receipt"; do sha_of "$receipt"; done)
[ "$current_go_receipts" = "$go_receipts" ] || fail "Go analyzer receipts changed on reuse"
ok "Go analyzer reuse is byte-stable and download-free"

mv "$CASE_TOOLS/gremlins/0.5.0" "$TMP/gremlins-saved"
WORKSPACE_OFFLINE=1 expect_ok "warm Go cache rebuilds a missing analyzer offline" workspace install go
[ "$(wc -l <"$CASE_GO_LOG" | tr -d ' ')" = 4 ] || fail "offline analyzer rebuild call count differs"
tail -n 1 "$CASE_GO_LOG" | grep -F $'\toff\tinstall -mod=readonly' >/dev/null || \
  fail "Go offline proxy was not forced off"

mv "$CASE_TOOLS/go-arch-lint/1.15.0" "$TMP/go-arch-lint-saved"
FAKE_GO_OFFLINE_CACHE=cold WORKSPACE_OFFLINE=1 expect_fail \
  "cold Go cache blocks offline analyzer publication" "offline cache miss for go-arch-lint 1.15.0" \
  workspace install go
[ ! -e "$CASE_TOOLS/go-arch-lint/1.15.0" ] || fail "cold offline analyzer was published"
mv "$TMP/go-arch-lint-saved" "$CASE_TOOLS/go-arch-lint/1.15.0"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "corrupt govulncheck"' >"$govuln_bin"
chmod +x "$govuln_bin"
corrupt_govuln_sha=$(sha_of "$govuln_bin")
FAKE_GO_BUILD_MODE=fail expect_fail "failed Go analyzer repair preserves prior installation" \
  "failed to build managed Go tool govulncheck 1.1.4" workspace install go
[ "$(sha_of "$govuln_bin")" = "$corrupt_govuln_sha" ] || fail "failed analyzer repair changed prior bytes"
expect_ok "online Go analyzer repair atomically restores exact binary" workspace install go
"$govuln_bin" --version | grep -F 'govulncheck@v1.1.4' >/dev/null || fail "online analyzer repair differs"

new_go_fixture go-bad-metadata
FAKE_GO_METADATA_MODE=bad expect_fail "bad Go module metadata blocks publication" \
  "managed Go tool failed verification for govulncheck 1.1.4" workspace install go
[ ! -e "$CASE_TOOLS/govulncheck/1.1.4" ] || fail "bad-metadata analyzer was published"

new_rust_fixture rust-convergence
expect_ok "Rust profile installs the exact managed toolchain and cargo-modules" workspace install rust
rust_root=$CASE_TOOLS/rust/1.97.1
rust_cargo_home=$rust_root/cargo
rustup_home=$rust_root/rustup
rust_toolchain=$rustup_home/toolchains/1.97.1-$CASE_RUST_TARGET
cargo_modules_root=$CASE_TOOLS/cargo-modules/0.26.0
rust_receipt=$rust_root/.harness-workspace-receipt
cargo_modules_receipt=$cargo_modules_root/.harness-workspace-receipt
[ "$($rust_cargo_home/bin/rustc --version)" = 'rustc 1.97.1 (fake 2026-07-16)' ] || \
  fail "managed rustc probe differs"
[ "$($rust_cargo_home/bin/cargo --version)" = 'cargo 1.97.1 (fake 2026-07-16)' ] || \
  fail "managed cargo probe differs"
[ "$($rust_cargo_home/bin/rustfmt --version)" = 'rustfmt 1.97.1 (fake 2026-07-16)' ] || \
  fail "managed rustfmt probe differs"
[ "$($rust_cargo_home/bin/cargo-clippy --version)" = 'clippy 0.1.97 (fake 2026-07-16)' ] || \
  fail "managed Clippy probe differs"
[ "$($rust_toolchain/lib/rustlib/$CASE_RUST_TARGET/bin/llvm-cov --version)" = \
  'LLVM version 21.1.0 (fake)' ] || fail "managed llvm-cov probe differs"
[ "$($rust_toolchain/lib/rustlib/$CASE_RUST_TARGET/bin/llvm-profdata --version)" = \
  'LLVM version 21.1.0 (fake)' ] || fail "managed llvm-profdata probe differs"
[ "$($cargo_modules_root/bin/cargo-modules --version)" = 'cargo-modules 0.26.0' ] || \
  fail "managed cargo-modules probe differs"
[ -f "$rust_toolchain/lib/rustlib/$CASE_RUST_TARGET/lib/libstd-fixture.rlib" ] || \
  fail "managed rust-std payload is missing"
[ "$(awk -F '\t' -v platform="$CASE_RUST_PLATFORM" \
  '$1 == "component" && $2 == platform { count++ } END { print count + 0 }' \
  "$CASE_ROOT/.harness/rust-dist.lock")" = 6 ] || fail "selected Rust closure is not six components"
for component_sha in $(awk -F '\t' -v platform="$CASE_RUST_PLATFORM" \
  '$1 == "component" && $2 == platform { print $6 }' "$CASE_ROOT/.harness/rust-dist.lock"); do
  tree_has_sha "$CASE_CACHE/rust/dist" "$component_sha" || \
    fail "Rust component $component_sha is absent from the workspace cache"
  grep -F "$component_sha" "$rust_receipt" >/dev/null || \
    fail "Rust receipt omits component $component_sha"
done
dist_lock_sha=$(sha_of "$CASE_ROOT/.harness/rust-dist.lock")
cargo_lock_sha=$(sha_of "$CASE_ROOT/.harness/cargo-modules.lock")
crate_sha=$(sha_of "$CASE_CARGO_CRATE")
grep -F "$dist_lock_sha" "$rust_receipt" >/dev/null || fail "Rust receipt omits the distribution lock"
grep -F "$cargo_lock_sha" "$cargo_modules_receipt" >/dev/null || \
  fail "cargo-modules receipt omits its Cargo lock"
grep -F "$crate_sha" "$cargo_modules_receipt" >/dev/null || \
  fail "cargo-modules receipt omits its authenticated crate"
tree_has_sha "$CASE_CACHE/rust" "$crate_sha" || fail "authenticated cargo-modules crate is not cached"
grep -F "$CASE_TOOLS/rustup/1.28.2/bin/rustup-init" "$CASE_RUST_INIT_LOG" >/dev/null || \
  fail "ambient rustup-init was selected"
grep -F $'minimal/clippy,rustfmt,llvm-tools-preview' "$CASE_RUSTUP_LOG" >/dev/null || \
  fail "Rustup did not receive the exact profile and component closure"
grep -F "$CASE_TOOLS/rust/1.97.1/cargo/bin/cargo" "$CASE_CARGO_LOG" >/dev/null || \
  fail "cargo-modules did not use the absolute managed Cargo proxy"
grep -F "$CASE_TOOLS/rust/1.97.1/cargo/bin/cargo"$'\t'"$CASE_CACHE/cargo"$'\t'"$rustup_home" \
  "$CASE_CARGO_LOG" >/dev/null || fail "cargo-modules managed Cargo environment differs"
grep -F "$CASE_TOOLS/rust/1.97.1/cargo/bin/rustdoc" "$CASE_CARGO_LOG" >/dev/null || \
  fail "cargo-modules did not receive the absolute managed rustdoc proxy"
grep -F 'install locked=1 no-track=1 force=1 offline=0' "$CASE_CARGO_LOG" >/dev/null || \
  fail "cargo-modules install flags differ"
ok "Rust convergence uses the retained six-component closure and authenticated Cargo inputs"

prepare_exec_inspector
poison_exec_commands uv python3.13 lizard rustup rustc cargo rustfmt cargo-clippy \
  cargo-audit cargo-llvm-cov cargo-modules vulture pip-audit
if ! capture_exec 0 rust uv python3.13 lizard rustup rustc cargo rustfmt cargo-clippy \
  cargo-audit cargo-llvm-cov cargo-modules; then
  fail "Rust exec did not use its sealed managed environment"
fi
assert_exec_path_set "Rust profile" "$TMP/output" \
  "$CASE_TOOLS/uv/0.12.5/bin" \
  "$CASE_TOOLS/rustup/1.28.2/bin" \
  "$CASE_TOOLS/cargo-audit/0.22.2/bin" \
  "$CASE_TOOLS/cargo-llvm-cov/0.8.7/bin" \
  "$CASE_TOOLS/python/3.13.15/runtime/bin" \
  "$CASE_TOOLS/lizard/1.22.2/bin" \
  "$CASE_TOOLS/rust/1.97.1/cargo/bin" \
  "$CASE_TOOLS/cargo-modules/0.26.0/bin"
assert_exec_command "$TMP/output" rustup "$CASE_TOOLS/rust/1.97.1/cargo/bin/rustup"
assert_exec_command "$TMP/output" rustc "$CASE_TOOLS/rust/1.97.1/cargo/bin/rustc"
assert_exec_command "$TMP/output" cargo "$CASE_TOOLS/rust/1.97.1/cargo/bin/cargo"
assert_exec_command "$TMP/output" rustfmt "$CASE_TOOLS/rust/1.97.1/cargo/bin/rustfmt"
assert_exec_command "$TMP/output" cargo-clippy \
  "$CASE_TOOLS/rust/1.97.1/cargo/bin/cargo-clippy"
assert_exec_command "$TMP/output" cargo-audit "$CASE_TOOLS/cargo-audit/0.22.2/bin/cargo-audit"
assert_exec_command "$TMP/output" cargo-llvm-cov \
  "$CASE_TOOLS/cargo-llvm-cov/0.8.7/bin/cargo-llvm-cov"
assert_exec_command "$TMP/output" cargo-modules \
  "$CASE_TOOLS/cargo-modules/0.26.0/bin/cargo-modules"
assert_exec_env "$TMP/output" RUSTUP_HOME "$CASE_TOOLS/rust/1.97.1/rustup"
assert_exec_env "$TMP/output" RUSTUP_TOOLCHAIN "1.97.1-$CASE_RUST_TARGET"
assert_exec_env "$TMP/output" RUSTUP_AUTO_INSTALL 0
assert_path_excludes "$TMP/output" '/vulture/'
assert_path_excludes "$TMP/output" '/pip-audit/'
assert_poison_unused
ok "Rust exec resolves the exact managed toolchain and Cargo extensions"

rust_receipt_sha=$(sha_of "$rust_receipt")
cargo_modules_receipt_sha=$(sha_of "$cargo_modules_receipt")
rust_curl_count=$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')
rust_init_count=$(wc -l <"$CASE_RUST_INIT_LOG" | tr -d ' ')
rustup_count=$(wc -l <"$CASE_RUSTUP_LOG" | tr -d ' ')
cargo_build_count=$(wc -l <"$CASE_CARGO_LOG" | tr -d ' ')
expect_ok "second Rust install reuses the exact toolchain and cargo-modules" workspace install rust
WORKSPACE_OFFLINE=1 expect_ok "warm offline Rust install reuses the exact workspace" workspace install rust
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = "$rust_curl_count" ] || fail "Rust reuse downloaded"
[ "$(wc -l <"$CASE_RUST_INIT_LOG" | tr -d ' ')" = "$rust_init_count" ] || fail "Rust reuse invoked rustup-init"
[ "$(wc -l <"$CASE_RUSTUP_LOG" | tr -d ' ')" = "$rustup_count" ] || fail "Rust reuse invoked rustup"
[ "$(wc -l <"$CASE_CARGO_LOG" | tr -d ' ')" = "$cargo_build_count" ] || fail "Rust reuse invoked Cargo"
[ "$(sha_of "$rust_receipt")" = "$rust_receipt_sha" ] || fail "Rust receipt changed on reuse"
[ "$(sha_of "$cargo_modules_receipt")" = "$cargo_modules_receipt_sha" ] || \
  fail "cargo-modules receipt changed on reuse"
ok "Rust and cargo-modules reuse is byte-stable and download-free"

mv "$rust_root" "$TMP/rust-toolchain-saved"
WORKSPACE_OFFLINE=1 expect_ok "warm component cache rebuilds a missing Rust toolchain offline" \
  workspace install rust
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = "$rust_curl_count" ] || \
  fail "offline Rust rebuild downloaded"
[ "$(wc -l <"$CASE_RUST_INIT_LOG" | tr -d ' ')" = "$((rust_init_count + 1))" ] || \
  fail "offline Rust rebuild did not invoke rustup-init once"
[ "$(wc -l <"$CASE_RUSTUP_LOG" | tr -d ' ')" = "$((rustup_count + 1))" ] || \
  fail "offline Rust rebuild did not invoke rustup once"
[ "$(sha_of "$rust_receipt")" = "$rust_receipt_sha" ] || fail "offline Rust receipt differs"
ok "Rust toolchain rebuild consumes only the verified workspace component cache"

mv "$cargo_modules_root" "$TMP/cargo-modules-saved"
WORKSPACE_OFFLINE=1 expect_ok "warm crate and registry caches rebuild cargo-modules offline" \
  workspace install rust
[ "$(wc -l <"$CASE_CURL_LOG" | tr -d ' ')" = "$rust_curl_count" ] || \
  fail "offline cargo-modules rebuild downloaded"
[ "$(wc -l <"$CASE_CARGO_LOG" | tr -d ' ')" = "$((cargo_build_count + 1))" ] || \
  fail "offline cargo-modules rebuild did not invoke Cargo once"
tail -n 1 "$CASE_CARGO_LOG" | grep -F \
  'install locked=1 no-track=1 force=1 offline=1' >/dev/null || \
  fail "Cargo offline flags were not forwarded"
[ "$(sha_of "$cargo_modules_receipt")" = "$cargo_modules_receipt_sha" ] || \
  fail "offline cargo-modules receipt differs"
ok "cargo-modules rebuild is locked, offline, and uses managed Cargo"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "corrupt rustc"' >"$rust_cargo_home/bin/rustc"
chmod +x "$rust_cargo_home/bin/rustc"
corrupt_rustc_sha=$(sha_of "$rust_cargo_home/bin/rustc")
FAKE_RUSTUP_INSTALL_MODE=fail expect_rejected \
  "failed Rust repair preserves the prior toolchain" workspace install rust
[ "$(sha_of "$rust_cargo_home/bin/rustc")" = "$corrupt_rustc_sha" ] || \
  fail "failed Rust repair changed prior bytes"
FAKE_RUSTUP_PAYLOAD_MODE=missing expect_rejected \
  "bad Rust payload repair preserves the prior toolchain" workspace install rust
[ "$(sha_of "$rust_cargo_home/bin/rustc")" = "$corrupt_rustc_sha" ] || \
  fail "bad Rust payload repair changed prior bytes"
expect_ok "exact Rust repair atomically replaces the corrupt toolchain" workspace install rust
[ "$($rust_cargo_home/bin/rustc --version)" = 'rustc 1.97.1 (fake 2026-07-16)' ] || \
  fail "exact Rust repair differs"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "corrupt cargo-modules"' \
  >"$cargo_modules_root/bin/cargo-modules"
chmod +x "$cargo_modules_root/bin/cargo-modules"
corrupt_cargo_modules_sha=$(sha_of "$cargo_modules_root/bin/cargo-modules")
FAKE_CARGO_BUILD_MODE=fail expect_rejected \
  "failed cargo-modules repair preserves the prior command" workspace install rust
[ "$(sha_of "$cargo_modules_root/bin/cargo-modules")" = "$corrupt_cargo_modules_sha" ] || \
  fail "failed cargo-modules repair changed prior bytes"
FAKE_CARGO_PAYLOAD_MODE=missing expect_rejected \
  "bad cargo-modules payload repair preserves the prior command" workspace install rust
[ "$(sha_of "$cargo_modules_root/bin/cargo-modules")" = "$corrupt_cargo_modules_sha" ] || \
  fail "bad cargo-modules payload repair changed prior bytes"
expect_ok "exact cargo-modules repair atomically restores the command" workspace install rust
[ "$($cargo_modules_root/bin/cargo-modules --version)" = 'cargo-modules 0.26.0' ] || \
  fail "exact cargo-modules repair differs"

new_rust_fixture rust-cold-component-cache
expect_ok "cold-cache Rust fixture first converges online" workspace install rust
mv "$CASE_TOOLS/rust/1.97.1" "$TMP/cold-rust-toolchain-saved"
mv "$CASE_TOOLS/cargo-modules/0.26.0" "$TMP/cold-cargo-modules-saved"
mv "$CASE_CACHE/rust" "$TMP/cold-rust-cache-saved"
WORKSPACE_OFFLINE=1 expect_fail "cold Rust component cache refuses before publication" \
  "offline cache miss" workspace install rust
[ ! -e "$CASE_TOOLS/rust/1.97.1" ] || fail "cold offline Rust toolchain was published"
[ ! -e "$CASE_TOOLS/cargo-modules/0.26.0" ] || fail "cold offline cargo-modules was published"

new_fixture hooks-refusal
pre_commit=$CASE_ROOT/.git/hooks/pre-commit
pre_push=$CASE_ROOT/.git/hooks/pre-push
printf '%s\n' '#!/bin/sh' 'echo unmanaged' >"$pre_commit"
printf '%s\n' '#!/bin/sh' 'exec make pre-push' >"$pre_push"
chmod +x "$pre_commit" "$pre_push"
before_commit=$(sha_of "$pre_commit")
before_push=$(sha_of "$pre_push")
expect_fail "unmanaged hook refuses both hook writes" "$pre_commit" workspace install-hooks
[ "$(sha_of "$pre_commit")" = "$before_commit" ] || fail "unmanaged pre-commit changed"
[ "$(sha_of "$pre_push")" = "$before_push" ] || fail "pre-push changed during refusal"

new_fixture hooks-before-download
pre_commit=$CASE_ROOT/.git/hooks/pre-commit
printf '%s\n' '#!/bin/sh' 'echo unmanaged' >"$pre_commit"
chmod +x "$pre_commit"
expect_fail "hook collision fails before tool download" "$pre_commit" workspace install
[ ! -e "$CASE_CURL_LOG" ] || fail "hook collision allowed a download"

new_fixture hooks-pre-push-refusal
pre_commit=$CASE_ROOT/.git/hooks/pre-commit
pre_push=$CASE_ROOT/.git/hooks/pre-push
printf '%s\n' '#!/bin/sh' 'uv run harness pre-commit' >"$pre_commit"
printf '%s\n' '#!/bin/sh' 'echo unmanaged' >"$pre_push"
chmod 0755 "$pre_commit" "$pre_push"
before_commit=$(sha_of "$pre_commit")
before_push=$(sha_of "$pre_push")
expect_fail "unmanaged pre-push refuses both hook writes" "$pre_push" workspace install-hooks
[ "$(sha_of "$pre_commit")" = "$before_commit" ] || fail "pre-commit changed during pre-push refusal"
[ "$(sha_of "$pre_push")" = "$before_push" ] || fail "unmanaged pre-push changed"
[ "$(mode_of "$pre_commit")" = 755 ] || fail "pre-commit mode changed during pre-push refusal"
[ "$(mode_of "$pre_push")" = 755 ] || fail "unmanaged pre-push mode changed"

new_fixture hooks-near-match
pre_commit=$CASE_ROOT/.git/hooks/pre-commit
pre_push=$CASE_ROOT/.git/hooks/pre-push
printf '%s\n' '#!/bin/sh' 'uv run harness pre-commit ' >"$pre_commit"
printf '%s\n' '#!/bin/sh' 'cargo harness pre-push' >"$pre_push"
chmod 0644 "$pre_commit"
chmod 0755 "$pre_push"
before_commit=$(sha_of "$pre_commit")
before_push=$(sha_of "$pre_push")
expect_fail "near-match hook refuses before download or mutation" "$pre_commit" workspace install
[ ! -e "$CASE_CURL_LOG" ] || fail "near-match hook collision allowed a download"
[ ! -e "$CASE_TOOLS/probe/1.0.0" ] || fail "near-match hook collision installed a tool"
[ ! -e "$CASE_HOME/.claude/skills/harness" ] || fail "near-match hook collision deployed a skill"
[ "$(sha_of "$pre_commit")" = "$before_commit" ] || fail "near-match pre-commit changed"
[ "$(sha_of "$pre_push")" = "$before_push" ] || fail "pre-push changed during near-match refusal"
[ "$(mode_of "$pre_commit")" = 644 ] || fail "near-match pre-commit mode changed"
[ "$(mode_of "$pre_push")" = 755 ] || fail "pre-push mode changed during near-match refusal"
ok "near-match refusal preserves both hook bytes and modes"

legacy_index=0
for legacy_runner in 'uv run harness' 'bun harness.ts' 'go run harness.go' 'cargo harness'; do
  for hook_name in pre-commit pre-push; do
    legacy_index=$((legacy_index + 1))
    new_fixture "hooks-no-exec-$legacy_index"
    pre_commit=$CASE_ROOT/.git/hooks/pre-commit
    pre_push=$CASE_ROOT/.git/hooks/pre-push
    if [ "$hook_name" = pre-commit ]; then
      candidate=$pre_commit
      other=$pre_push
      other_name=pre-push
    else
      candidate=$pre_push
      other=$pre_commit
      other_name=pre-commit
    fi
    printf '%s\n' '#!/bin/sh' "$legacy_runner $hook_name" >"$candidate"
    printf '%s\n' '#!/bin/sh' "exec make $other_name" >"$other"
    chmod 0755 "$candidate" "$other"
    expect_ok "historical no-exec $legacy_runner $hook_name migrates" workspace install-hooks
    managed_hook_is_exact "$candidate" "$hook_name" || \
      fail "historical no-exec $legacy_runner $hook_name did not migrate exactly"
    [ "$(mode_of "$candidate")" = 755 ] || \
      fail "migrated $legacy_runner $hook_name mode is not 0755"
    if [ "$hook_name" = pre-push ]; then
      capture=$TMP/pre-push-stdin-$legacy_index
      printf '%s\n' "stdin survives $legacy_index" | (
        cd "$CASE_ROOT"
        PREPUSH_CAPTURE="$capture" "$candidate"
      )
      [ "$(<"$capture")" = "stdin survives $legacy_index" ] || \
        fail "migrated $legacy_runner pre-push did not preserve stdin"
    fi
  done
done

legacy_index=0
for legacy_runner in 'uv run pre-commit' 'bun harness.ts --pre-commit'; do
  legacy_index=$((legacy_index + 1))
  new_fixture "hooks-old-pre-commit-$legacy_index"
  pre_commit=$CASE_ROOT/.git/hooks/pre-commit
  pre_push=$CASE_ROOT/.git/hooks/pre-push
  printf '%s\n' '#!/bin/sh' "$legacy_runner" >"$pre_commit"
  printf '%s\n' '#!/bin/sh' 'exec make pre-push' >"$pre_push"
  chmod 0755 "$pre_commit" "$pre_push"
  expect_ok "older historical $legacy_runner migrates" workspace install-hooks
  managed_hook_is_exact "$pre_commit" pre-commit || \
    fail "older historical $legacy_runner did not migrate exactly"
  [ "$(mode_of "$pre_commit")" = 755 ] || \
    fail "migrated older historical $legacy_runner mode is not 0755"
done

new_fixture hooks
pre_commit=$CASE_ROOT/.git/hooks/pre-commit
pre_push=$CASE_ROOT/.git/hooks/pre-push
printf '%s\n' '#!/bin/sh' 'exec uv run harness pre-commit' >"$pre_commit"
printf '%s\n' '#!/bin/sh' 'exec cargo harness pre-push' >"$pre_push"
chmod +x "$pre_commit" "$pre_push"
expect_ok "exact legacy hooks migrate" workspace install-hooks
grep -F 'root=$(git rev-parse --show-toplevel)' "$pre_commit" >/dev/null || fail "pre-commit does not enter root"
grep -F 'exec make pre-push' "$pre_push" >/dev/null || fail "pre-push does not call Make"
[ -x "$pre_commit" ] && [ -x "$pre_push" ] || fail "managed hooks are not executable"
hook_commit_sha=$(sha_of "$pre_commit")
hook_push_sha=$(sha_of "$pre_push")
expect_ok "managed hook rerun succeeds" workspace install-hooks
[ "$(sha_of "$pre_commit")" = "$hook_commit_sha" ] || fail "pre-commit rerun changed bytes"
[ "$(sha_of "$pre_push")" = "$hook_push_sha" ] || fail "pre-push rerun changed bytes"
capture=$TMP/pre-push-stdin
printf '%s\n' 'stdin survives' | (
  cd "$CASE_ROOT"
  PREPUSH_CAPTURE="$capture" "$pre_push"
)
[ "$(<"$capture")" = 'stdin survives' ] || fail "pre-push stdin was not preserved"
ok "managed hooks are executable, byte-idempotent, and preserve pre-push stdin"

new_fixture hooks-path
git -C "$CASE_ROOT" config core.hooksPath .custom-hooks
expect_ok "core.hooksPath is honored" workspace install-hooks
[ -x "$CASE_ROOT/.custom-hooks/pre-commit" ] && [ -x "$CASE_ROOT/.custom-hooks/pre-push" ] || \
  fail "core.hooksPath hooks missing"

new_fixture worktree
git -C "$CASE_ROOT" config --unset core.hooksPath
main_root=$CASE_ROOT
worktree_root=$TMP/linked-worktree
git -C "$main_root" worktree add -q -b workspace-test-worktree "$worktree_root"
CASE_ROOT=$worktree_root
CASE_LOCK=$CASE_ROOT/.harness/workspace.lock
expect_ok "linked worktree hook destinations resolve" workspace install-hooks
worktree_pre_push=$(git -C "$CASE_ROOT" rev-parse --git-path hooks/pre-push)
case $worktree_pre_push in
  /*) ;;
  *) worktree_pre_push=$CASE_ROOT/$worktree_pre_push ;;
esac
[ -x "$worktree_pre_push" ] || fail "linked worktree pre-push hook missing"
capture=$TMP/worktree-pre-push-stdin
printf '%s\n' 'worktree stdin' | (
  cd "$CASE_ROOT"
  PREPUSH_CAPTURE="$capture" "$worktree_pre_push"
)
[ "$(<"$capture")" = 'worktree stdin' ] || fail "worktree hook did not preserve stdin"
ok "linked worktree hook enters its own Git root"

new_fixture stop-structure
cat >"$CASE_ROOT/.claude/settings.json" <<'EOF'
{
  "hooks": {"Stop": []},
  "description": "cd $CLAUDE_PROJECT_DIR && make stop-hook"
}
EOF
git -C "$CASE_ROOT" add .claude/settings.json
git -C "$CASE_ROOT" commit -qm misplaced-stop-command
expect_fail "Stop command text outside the hook structure is rejected" \
  "Claude Stop configuration must contain the managed command hook" workspace preflight
cat >"$CASE_ROOT/.claude/settings.json" <<'EOF'
{
  "hooks": {
    "Stop": [{
      "hooks": [
        {"type": "command"},
        {"command": "cd $CLAUDE_PROJECT_DIR && make stop-hook"}
      ]
    }]
  }
}
EOF
git -C "$CASE_ROOT" add .claude/settings.json
git -C "$CASE_ROOT" commit -qm split-stop-command
expect_fail "Stop type and command must share one hook object" \
  "Claude Stop configuration must contain the managed command hook" workspace preflight

new_fixture stop-wrapper
printf '%s\n' '# tampered' >>"$CASE_ROOT/.codex/hooks/codex-stop-hook.sh"
git -C "$CASE_ROOT" add .codex/hooks/codex-stop-hook.sh
git -C "$CASE_ROOT" commit -qm tampered-wrapper
expect_fail "modified Codex Stop wrapper is rejected" \
  "Codex Stop wrapper differs from the managed implementation" workspace preflight

new_fixture skills
mkdir -p "$CASE_HOME/.claude/skills/harness" "$CASE_HOME/.claude/skills/unrelated" \
  "$CASE_HOME/.agents/skills/harness" "$CASE_HOME/.agents/skills/unrelated"
printf '%s\n' stale >"$CASE_HOME/.claude/skills/harness/stale.md"
printf '%s\n' stale >"$CASE_HOME/.agents/skills/harness/stale.md"
printf '%s\n' keep >"$CASE_HOME/.claude/skills/unrelated/keep.md"
printf '%s\n' keep >"$CASE_HOME/.agents/skills/unrelated/keep.md"
expect_ok "managed skills deploy exactly" workspace sync-skills
[ ! -e "$CASE_HOME/.claude/skills/harness/stale.md" ] || fail "stale Claude skill file remains"
[ ! -e "$CASE_HOME/.agents/skills/harness/stale.md" ] || fail "stale agent skill file remains"
[ -f "$CASE_HOME/.claude/skills/unrelated/keep.md" ] || fail "unrelated Claude skill removed"
[ -f "$CASE_HOME/.agents/skills/unrelated/keep.md" ] || fail "unrelated agent skill removed"
diff -qr "$CASE_ROOT/skills/harness" "$CASE_HOME/.claude/skills/harness" >/dev/null || fail "Claude skill differs"
diff -qr "$CASE_ROOT/skills/harness" "$CASE_HOME/.agents/skills/harness" >/dev/null || fail "agent skill differs"
expect_ok "managed skill rerun is idempotent" workspace sync-skills

new_fixture embedded-skills
mkdir -p "$CASE_ROOT/.harness/skills"
mv "$CASE_ROOT/skills/harness" "$CASE_ROOT/.harness/skills/harness"
rmdir "$CASE_ROOT/skills"
git -C "$CASE_ROOT" add -A
git -C "$CASE_ROOT" commit -qm embedded-skill
expect_ok "embedded skill source deploys" workspace sync-skills
diff -qr "$CASE_ROOT/.harness/skills/harness" "$CASE_HOME/.claude/skills/harness" >/dev/null || \
  fail "embedded Claude skill differs"
diff -qr "$CASE_ROOT/.harness/skills/harness" "$CASE_HOME/.agents/skills/harness" >/dev/null || \
  fail "embedded agent skill differs"

expect_ok "verification fixture installs tool" workspace install
expect_ok "verification fixture installs hooks" workspace install-hooks
expect_ok "full phase-one verification passes" workspace verify

new_fixture verify-git-state
printf '%s\n' 'worktree original' >"$CASE_ROOT/work tree.txt"
printf '%s\n' 'delete original' >"$CASE_ROOT/work deleted.txt"
printf '%s\n' 'index original' >"$CASE_ROOT/index staged.txt"
printf '%s\n' 'rename original' >"$CASE_ROOT/index old.txt"
git -C "$CASE_ROOT" add .
git -C "$CASE_ROOT" commit -qm tracked-verification-files
expect_ok "Git-state verification fixture installs tool" workspace install
expect_ok "Git-state verification fixture installs hooks" workspace install-hooks
expect_ok "Git-state verification fixture deploys skills" workspace sync-skills
printf '%s\n' 'unrelated untracked bytes' >"$CASE_ROOT/unrelated untracked.txt"
untracked_sha=$(sha_of "$CASE_ROOT/unrelated untracked.txt")
expect_ok "clean verification permits unrelated untracked files" workspace verify

printf '%s\n' 'worktree changed' >"$CASE_ROOT/work tree.txt"
rm "$CASE_ROOT/work deleted.txt"
printf '%s\n' 'index changed' >"$CASE_ROOT/index staged.txt"
git -C "$CASE_ROOT" add "index staged.txt"
git -C "$CASE_ROOT" mv "index old.txt" "index renamed.txt"
if workspace verify >"$TMP/output" 2>&1; then
  fail "verification rejects changed tracked Git state (unexpected success)"
fi
for expected in \
  'M  "index staged.txt"' \
  'R  "index old.txt" -> "index renamed.txt"' \
  ' D "work deleted.txt"' \
  ' M "work tree.txt"' \
  'tracked worktree and/or index changed during workspace setup'; do
  grep -F "$expected" "$TMP/output" >/dev/null || \
    fail "verification reports changed tracked Git state (missing: $expected)"
done
[ "$(<"$CASE_ROOT/work tree.txt")" = 'worktree changed' ] || \
  fail "verification reverted the unstaged worktree modification"
[ ! -e "$CASE_ROOT/work deleted.txt" ] || \
  fail "verification restored the unstaged worktree deletion"
[ "$(git -C "$CASE_ROOT" show ':index staged.txt')" = 'index changed' ] || \
  fail "verification reverted the staged index modification"
[ ! -e "$CASE_ROOT/index old.txt" ] && [ -f "$CASE_ROOT/index renamed.txt" ] || \
  fail "verification reverted the staged rename"
[ "$(sha_of "$CASE_ROOT/unrelated untracked.txt")" = "$untracked_sha" ] || \
  fail "verification changed the unrelated untracked file"
ok "verification reports every tracked status without reverting Git state"

printf '1..%d\n' "$passed"
