#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
runner_source="$repo_root/go/harness.go"

managed_go=${HARNESS_TEST_GO:-}
if [ -z "$managed_go" ] && [ -x /private/tmp/harness-task23-go/go/bin/go ]; then
  managed_go=/private/tmp/harness-task23-go/go/bin/go
fi
if [ -z "$managed_go" ]; then
  tools_root=${HARNESS_TOOLS_ROOT:-$HOME/.local/share/harness/tools}
  candidate="$tools_root/go/1.27.0/go/bin/go"
  if [ -x "$candidate" ]; then
    managed_go=$candidate
  fi
fi
if [ -z "$managed_go" ] || [ ! -x "$managed_go" ]; then
  echo "managed Go 1.27.0 not found; set HARNESS_TEST_GO" >&2
  exit 1
fi
case "$($managed_go version)" in
  "go version go1.27.0 "*) ;;
  *)
    echo "expected managed Go 1.27.0 at $managed_go" >&2
    exit 1
    ;;
esac

tmp=$(mktemp -d "${TMPDIR:-/tmp}/harness-go-runner.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

fixture="$tmp/repo"
fake_bin="$tmp/bin"
command_log="$tmp/commands.log"
workspace_log="$tmp/workspace.log"
mkdir -p "$fixture/.harness" "$fake_bin" "$tmp/gocache" "$tmp/gopath"
: >"$command_log"
: >"$workspace_log"

(
  cd "$repo_root/go"
  GOCACHE="$tmp/gocache" \
    GOENV=off \
    GOFLAGS=-mod=readonly \
    GOPATH="$tmp/gopath" \
    GOTOOLCHAIN=local \
    "$managed_go" build -o "$tmp/harness-go" harness.go
)

cat >"$tmp/fake-tool" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

tool=${0##*/}
{
  printf '[%s]' "$tool"
  for arg in "$@"; do
    printf '[%s]' "$arg"
  done
  printf '\n'
} >>"$HARNESS_CMD_LOG"

case "$tool" in
  go)
    if [ "${1:-}" = test ]; then
      exit 0
    fi
    echo "poisoned go launcher invoked: $*" >&2
    exit 97
    ;;
  uvx)
    echo "poisoned uvx launcher invoked: $*" >&2
    exit 98
    ;;
  lizard)
    case " $* " in
      *" --csv "*)
        printf '%s\n' '1,1,1,0,0,"foo@1-1@sample.go",foo,0,0,0,0'
        ;;
    esac
    ;;
esac
EOF
chmod +x "$tmp/fake-tool"
for tool in go uvx govulncheck go-arch-lint gremlins lizard; do
  cp "$tmp/fake-tool" "$fake_bin/$tool"
done

cat >"$fixture/.harness/workspace.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf '[OFFLINE=%s]' "${OFFLINE:-}"
  for arg in "$@"; do
    printf '[%s]' "$arg"
  done
  printf '\n'
} >>"$HARNESS_WORKSPACE_LOG"
EOF
chmod +x "$fixture/.harness/workspace.sh"

cat >"$fixture/go.mod" <<'EOF'
module example

go 1.24
EOF
cat >"$fixture/sample.go" <<'EOF'
package example

func foo() {}
EOF
cat >"$fixture/coverage.out" <<'EOF'
mode: set
example/sample.go:1.1,1.10 1 1
EOF
: >"$fixture/.go-arch-lint.yml"
touch -t 202001010000 "$fixture/sample.go"
touch -t 202001010001 "$fixture/coverage.out"

run_task() {
  (
    cd "$fixture"
    HARNESS_CMD_LOG="$command_log" \
      HARNESS_WORKSPACE_LOG="$workspace_log" \
      PATH="$fake_bin:/usr/bin:/bin" \
      "$tmp/harness-go" "$@"
  )
}

run_task audit
run_task arch
run_task complexity
run_task mutation
run_task crap
(
  cd "$fixture"
  HARNESS_CMD_LOG="$command_log" \
    HARNESS_WORKSPACE_LOG="$workspace_log" \
    OFFLINE=1 \
    PATH="$fake_bin:/usr/bin:/bin" \
    "$tmp/harness-go" setup-hooks
)

cat >"$tmp/expected-commands.log" <<'EOF'
[govulncheck][./...]
[go-arch-lint][check]
[lizard][-l][go][.][-C][15][-a][8][-L][100][-i][0][-x][*_test.go][-x][./harness.go]
[go][test][-count=1][./...]
[gremlins][unleash][--timeout-coefficient=10][./suppressions]
[lizard][-l][go][.][--csv]
EOF
diff -u "$tmp/expected-commands.log" "$command_log"

cat >"$tmp/expected-workspace.log" <<'EOF'
[OFFLINE=1][install-hooks]
EOF
diff -u "$tmp/expected-workspace.log" "$workspace_log"

if grep -Eq '"uvx"|govulncheck@|go-arch-lint@|gremlins@' "$runner_source"; then
  echo "Go runner still contains an ambient/versioned analyzer launcher" >&2
  exit 1
fi

echo "go runner managed tool vectors: ok"
