#!/usr/bin/env bash
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORKFLOWS="
python/.github/workflows/ci.yml
bun/.github/workflows/ci.yml
go/.github/workflows/ci.yml
rust/.github/workflows/ci.yml
monorepo/.github/workflows/ci.yml
"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

count_matches() {
    pattern=$1
    file=$2
    awk -v pattern="$pattern" '$0 ~ pattern { count++ } END { print count + 0 }' "$file"
}

for relative_path in $WORKFLOWS; do
    workflow="$ROOT_DIR/$relative_path"
    [ -f "$workflow" ] || fail "$relative_path is missing"

    run_commands=$(awk '
        /^[[:space:]]*run:[[:space:]]*/ {
            sub(/^[[:space:]]*run:[[:space:]]*/, "")
            sub(/[[:space:]]*$/, "")
            print
        }
    ' "$workflow")
    expected_commands=$(printf '%s\n%s' 'make workspace' 'make ci')
    [ "$run_commands" = "$expected_commands" ] ||
        fail "$relative_path must run exactly make workspace then make ci"

    checkout_count=$(count_matches '^[[:space:]]*- uses:[[:space:]]*actions/checkout@' "$workflow")
    [ "$checkout_count" -eq 1 ] || fail "$relative_path must use checkout exactly once"

    full_history_count=$(count_matches '^[[:space:]]*fetch-depth:[[:space:]]*0[[:space:]]*$' "$workflow")
    [ "$full_history_count" -eq 1 ] || fail "$relative_path must check out full history"

    if grep -Eiq '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]*[^#]*(setup-uv|setup-python|setup-bun|setup-go|rust-toolchain|install-action)(@|/)' "$workflow"; then
        fail "$relative_path must not use floating runtime or installer actions"
    fi

    if grep -Eiq '^[[:space:]]*run:[[:space:]]*[^#]*(uv[[:space:]]+(python[[:space:]]+install|sync)|bun[[:space:]]+install|go[[:space:]]+mod[[:space:]]+download|cargo[[:space:]]+(fetch|build)|rustup[[:space:]]|curl[[:space:]])' "$workflow"; then
        fail "$relative_path must not install or sync tools and dependencies directly"
    fi

    if grep -Eiq '^[[:space:]]*run:[[:space:]]*[^#]*(harness(\.(py|ts|go))?|cargo[[:space:]]+harness)[[:space:]]+ci([[:space:]]|$)' "$workflow"; then
        fail "$relative_path must invoke CI through make ci"
    fi

    printf 'PASS: %s\n' "$relative_path"
done

ROOT_WORKFLOW="$ROOT_DIR/.github/workflows/workspace.yml"
[ -f "$ROOT_WORKFLOW" ] || fail ".github/workflows/workspace.yml is missing"

root_job_names=$(awk '
    $0 == "jobs:" { in_jobs = 1; next }
    in_jobs && /^[^[:space:]#]/ { in_jobs = 0 }
    in_jobs && /^  [[:alnum:]_-]+:[[:space:]]*$/ {
        line = $0
        sub(/^  /, "", line)
        sub(/:[[:space:]]*$/, "", line)
        print line
    }
' "$ROOT_WORKFLOW")
expected_root_jobs=$(printf '%s\n%s' 'linux_x86_64' 'macos_arm64')
[ "$root_job_names" = "$expected_root_jobs" ] ||
    fail ".github/workflows/workspace.yml must define exactly linux_x86_64 and macos_arm64 jobs"

count_job_lines() {
    job=$1
    expected=$2
    file=$3
    awk -v header="  $job:" -v expected="$expected" '
        $0 == header { in_job = 1; next }
        in_job && /^  [[:alnum:]_-]+:[[:space:]]*$/ { in_job = 0 }
        in_job {
            line = $0
            sub(/^[[:space:]]*/, "", line)
            if (line == expected) count++
        }
        END { print count + 0 }
    ' "$file"
}

job_step_names() {
    job=$1
    file=$2
    awk -v header="  $job:" '
        $0 == header { in_job = 1; next }
        in_job && /^  [[:alnum:]_-]+:[[:space:]]*$/ { in_job = 0 }
        in_job && /^      - name:[[:space:]]*/ {
            line = $0
            sub(/^      - name:[[:space:]]*/, "", line)
            print line
        }
    ' "$file"
}

job_block() {
    job=$1
    file=$2
    awk -v header="  $job:" '
        $0 == header { in_job = 1; next }
        in_job && /^  [[:alnum:]_-]+:[[:space:]]*$/ { exit }
        in_job { print }
    ' "$file"
}

expected_steps=$(printf '%s\n' \
    'Check out repository' \
    'Assert runner platform' \
    'First online convergence' \
    'Second online convergence without downloads' \
    'Warm offline convergence' \
    'Run CI gate' \
    'Verify tracked tree is clean')

for job in linux_x86_64 macos_arm64; do
    case "$job" in
        linux_x86_64)
            runner=ubuntu-24.04
            operating_system=Linux
            architecture=x86_64
            ;;
        macos_arm64)
            runner=macos-26
            operating_system=Darwin
            architecture=arm64
            ;;
    esac

    [ "$(count_job_lines "$job" "runs-on: $runner" "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must use $runner exactly once"
    [ "$(count_job_lines "$job" 'uses: actions/checkout@v4' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must use checkout exactly once"
    [ "$(count_job_lines "$job" 'fetch-depth: 0' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must check out full history"
    [ "$(job_step_names "$job" "$ROOT_WORKFLOW")" = "$expected_steps" ] ||
        fail "$job phases must appear in the required order"

    [ "$(count_job_lines "$job" "test \"\$(uname -s)\" = \"$operating_system\"" "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must assert uname -s is $operating_system"
    [ "$(count_job_lines "$job" "test \"\$(uname -m)\" = \"$architecture\"" "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must assert uname -m is $architecture"

    [ "$(count_job_lines "$job" 'run: make workspace' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must run the first online convergence exactly once"
    [ "$(count_job_lines "$job" 'PATH="$shim_dir:$PATH" make workspace' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must put the poisoned curl first on PATH for the second convergence"
    [ "$(count_job_lines "$job" 'run: make workspace OFFLINE=1' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must run warm offline convergence exactly once"
    [ "$(count_job_lines "$job" 'run: make ci' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must run make ci exactly once"
    [ "$(count_job_lines "$job" 'run: test -z "$(git status --porcelain --untracked-files=no)"' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must end with the exact tracked/index cleanliness assertion"

    [ "$(count_job_lines "$job" 'shim_dir=$(mktemp -d)' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must create the curl shim in a temporary directory"
    [ "$(count_job_lines "$job" "trap 'rm -rf \"\$shim_dir\"' EXIT" "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must clean up the temporary curl shim"
    [ "$(count_job_lines "$job" 'chmod 0755 "$shim_dir/curl"' "$ROOT_WORKFLOW")" -eq 1 ] ||
        fail "$job must make the poisoned curl executable"
    if ! job_block "$job" "$ROOT_WORKFLOW" |
        grep -Fq "printf '%s\\n' '#!/bin/sh' 'exit 97' > \"\$shim_dir/curl\""; then
        fail "$job curl shim must fail every attempted download"
    fi
done

if grep -Eiq '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]*[^#]*(setup-uv|setup-python|setup-bun|setup-go|rust-toolchain|install-action|actions/cache)(@|/)' "$ROOT_WORKFLOW"; then
    fail ".github/workflows/workspace.yml must not use floating setup or cache actions"
fi

if grep -Eiq '^[[:space:]]*(run:[[:space:]]*)?(sudo|brew|apt(-get)?|dnf|yum|apk|pip|npm|pnpm|yarn|uv[[:space:]]+(python[[:space:]]+install|sync)|bun[[:space:]]+install|go[[:space:]]+(install|mod[[:space:]]+download)|cargo[[:space:]]+(install|fetch|build)|rustup[[:space:]]|curl[[:space:]])' "$ROOT_WORKFLOW"; then
    fail ".github/workflows/workspace.yml must not install tools or dependencies directly"
fi

if grep -Eq '(enable-cache:|^[[:space:]]*cache:|GITHUB_(PATH|ENV)|\.(bashrc|zshrc|profile))' "$ROOT_WORKFLOW"; then
    fail ".github/workflows/workspace.yml must not mutate caches or shell profiles"
fi

printf 'PASS: .github/workflows/workspace.yml\n'
