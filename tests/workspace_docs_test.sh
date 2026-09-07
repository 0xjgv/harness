#!/usr/bin/env bash
# Guard the root public workspace contract against setup-documentation drift.

set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
README="$ROOT/README.md"
PYTHON_README="$ROOT/python/README.md"
PYTHON_CLAUDE_DOC="$ROOT/python/CLAUDE.md"
PYTHON_AGENTS_DOC="$ROOT/python/AGENTS.md"
BUN_README="$ROOT/bun/README.md"
BUN_CLAUDE_DOC="$ROOT/bun/CLAUDE.md"
BUN_AGENTS_DOC="$ROOT/bun/AGENTS.md"
GO_README="$ROOT/go/README.md"
RUST_README="$ROOT/rust/README.md"
MONOREPO_README="$ROOT/monorepo/README.md"
CLAUDE_DOC="$ROOT/CLAUDE.md"
AGENTS_DOC="$ROOT/AGENTS.md"
passed=0
failed=0
checked=0

pass() {
  checked=$((checked + 1))
  passed=$((passed + 1))
  printf 'ok %d - %s\n' "$checked" "$1"
}

fail() {
  checked=$((checked + 1))
  failed=$((failed + 1))
  printf 'not ok %d - %s\n' "$checked" "$1" >&2
}

contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$README"; then
    pass "$description"
  else
    fail "$description (missing: $needle)"
  fi
}

rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$README"; then
    fail "$description (found obsolete text: $needle)"
  else
    pass "$description"
  fi
}

rejects_getting_started() {
  description=$1
  needle=$2
  if printf '%s\n' "$getting_started" | grep -Fq -- "$needle"; then
    fail "$description (found obsolete setup text: $needle)"
  else
    pass "$description"
  fi
}

getting_started_sequence() {
  heading=$1
  block=$(awk -v heading="$heading" '
    $0 == heading { active = 1; next }
    active && /^### / { exit }
    active { print }
  ' "$README")
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$init_line" ] && [ -n "$commit_line" ] && [ -n "$workspace_line" ] &&
     [ "$init_line" -lt "$commit_line" ] && [ "$commit_line" -lt "$workspace_line" ]; then
    pass "$heading initializes and commits before make workspace"
  else
    fail "$heading must initialize and commit the repository before make workspace"
  fi
}

python_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$PYTHON_README"; then
    pass "$description"
  else
    fail "$description (missing from python/README.md: $needle)"
  fi
}

python_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$PYTHON_README"; then
    fail "$description (found obsolete Python text: $needle)"
  else
    pass "$description"
  fi
}

python_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eq -- "$pattern" "$PYTHON_README"; then
    fail "$description (matched obsolete Python pattern: $pattern)"
  else
    pass "$description"
  fi
}

python_claude_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$PYTHON_CLAUDE_DOC"; then
    pass "$description"
  else
    fail "$description (missing from python/CLAUDE.md: $needle)"
  fi
}

python_claude_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$PYTHON_CLAUDE_DOC"; then
    fail "$description (found obsolete Python agent guidance: $needle)"
  else
    pass "$description"
  fi
}

python_claude_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eiq -- "$pattern" "$PYTHON_CLAUDE_DOC"; then
    fail "$description (matched obsolete Python agent guidance: $pattern)"
  else
    pass "$description"
  fi
}

python_claude_ci_sequence() {
  block=$(awk '
    /^managed environment\. CI uses the same two commands/ { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$PYTHON_CLAUDE_DOC")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Python agent CI runs make workspace before make ci'
  else
    fail 'Python agent CI must run make workspace before make ci'
  fi
}

python_agents_md_drift_target() {
  fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/harness-python-drift.XXXXXX") || {
    fail 'Python agents-md-drift target creates its isolated fixture'
    return
  }
  fake_workspace="$fixture_dir/workspace.sh"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -eu'
    printf '%s\n' '[ "${OFFLINE:-0}" = 1 ] || { echo "expected OFFLINE=1" >&2; exit 1; }'
    printf '%s\n' '[ "$*" = "exec python -- uv run --frozen --no-sync harness agents-md-drift" ] || {'
    printf '%s\n' '  echo "unexpected managed command: $*" >&2'
    printf '%s\n' '  exit 1'
    printf '%s\n' '}'
    printf '%s\n' 'cmp -s CLAUDE.md AGENTS.md'
  } >"$fake_workspace"
  chmod 0755 "$fake_workspace"

  if output=$(make -s -C "$ROOT/python" --no-print-directory agents-md-drift \
      OFFLINE=1 WORKSPACE="$fake_workspace" 2>&1); then
    result=0
  else
    result=$?
  fi
  rm -R "$fixture_dir"

  if [ "$result" -eq 0 ]; then
    pass 'Python agents-md-drift runs through the offline managed Make boundary'
  else
    fail "Python agents-md-drift managed Make boundary failed: $output"
  fi
}

python_setup_sequence() {
  heading=$1
  block=$(awk -v heading="$heading" '
    $0 == heading { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$PYTHON_README")
  copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r python/ my-project" { print NR; exit }')
  customize_line=$(printf '%s\n' "$block" | awk '/^# (Edit|Customize) name and description / { print NR; exit }')
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$copy_line" ] && [ -n "$customize_line" ] && [ -n "$init_line" ] &&
     [ -n "$commit_line" ] && [ -n "$workspace_line" ] &&
     [ "$copy_line" -lt "$customize_line" ] &&
     [ "$customize_line" -lt "$init_line" ] &&
     [ "$init_line" -lt "$commit_line" ] &&
     [ "$commit_line" -lt "$workspace_line" ]; then
    pass "Python $heading copies and customizes before clean workspace setup"
  else
    fail "Python $heading must copy, customize, initialize, commit, then run make workspace"
  fi
}

python_ci_sequence() {
  block=$(awk '
    $0 == "### Continuous integration" { active = 1; next }
    active && /^(##|###) / { exit }
    active { print }
  ' "$PYTHON_README")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Python CI runs make workspace before make ci'
  else
    fail 'Python CI must run make workspace before make ci'
  fi
}

bun_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$BUN_README"; then
    pass "$description"
  else
    fail "$description (missing from bun/README.md: $needle)"
  fi
}

bun_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$BUN_README"; then
    fail "$description (found obsolete Bun text: $needle)"
  else
    pass "$description"
  fi
}

bun_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eq -- "$pattern" "$BUN_README"; then
    fail "$description (matched obsolete Bun pattern: $pattern)"
  else
    pass "$description"
  fi
}

bun_claude_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$BUN_CLAUDE_DOC"; then
    pass "$description"
  else
    fail "$description (missing from bun/CLAUDE.md: $needle)"
  fi
}

bun_claude_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$BUN_CLAUDE_DOC"; then
    fail "$description (found obsolete Bun agent guidance: $needle)"
  else
    pass "$description"
  fi
}

bun_claude_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eiq -- "$pattern" "$BUN_CLAUDE_DOC"; then
    fail "$description (matched obsolete Bun agent guidance: $pattern)"
  else
    pass "$description"
  fi
}

bun_claude_ci_sequence() {
  block=$(awk '
    /^managed environment\. CI uses the same two commands/ { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$BUN_CLAUDE_DOC")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Bun agent CI runs make workspace before make ci'
  else
    fail 'Bun agent CI must run make workspace before make ci'
  fi
}

bun_agents_md_drift_target() {
  fixture_dir=$(mktemp -d "${TMPDIR:-/tmp}/harness-bun-drift.XXXXXX") || {
    fail 'Bun agents-md-drift target creates its isolated fixture'
    return
  }
  fake_workspace="$fixture_dir/workspace.sh"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' 'set -eu'
    printf '%s\n' '[ "${OFFLINE:-0}" = 1 ] || { echo "expected OFFLINE=1" >&2; exit 1; }'
    printf '%s\n' '[ "$*" = "exec bun -- bun harness.ts agents-md-drift" ] || {'
    printf '%s\n' '  echo "unexpected managed command: $*" >&2'
    printf '%s\n' '  exit 1'
    printf '%s\n' '}'
    printf '%s\n' 'cmp -s CLAUDE.md AGENTS.md'
  } >"$fake_workspace"
  chmod 0755 "$fake_workspace"

  if output=$(make -s -C "$ROOT/bun" --no-print-directory agents-md-drift \
      OFFLINE=1 WORKSPACE="$fake_workspace" 2>&1); then
    result=0
  else
    result=$?
  fi
  rm -R "$fixture_dir"

  if [ "$result" -eq 0 ]; then
    pass 'Bun agents-md-drift runs through the offline managed Make boundary'
  else
    fail "Bun agents-md-drift managed Make boundary failed: $output"
  fi
}

bun_setup_sequence() {
  heading=$1
  block=$(awk -v heading="$heading" '
    $0 == heading { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$BUN_README")
  copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r bun/ my-project" { print NR; exit }')
  customize_line=$(printf '%s\n' "$block" | awk '/^# (Edit|Customize) name and description / { print NR; exit }')
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$copy_line" ] && [ -n "$customize_line" ] && [ -n "$init_line" ] &&
     [ -n "$commit_line" ] && [ -n "$workspace_line" ] &&
     [ "$copy_line" -lt "$customize_line" ] &&
     [ "$customize_line" -lt "$init_line" ] &&
     [ "$init_line" -lt "$commit_line" ] &&
     [ "$commit_line" -lt "$workspace_line" ]; then
    pass "Bun $heading copies and customizes before clean workspace setup"
  else
    fail "Bun $heading must copy, customize, initialize, commit, then run make workspace"
  fi
}

bun_ci_sequence() {
  block=$(awk '
    $0 == "### Continuous integration" { active = 1; next }
    active && /^(##|###) / { exit }
    active { print }
  ' "$BUN_README")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Bun CI runs make workspace before make ci'
  else
    fail 'Bun CI must run make workspace before make ci'
  fi
}

go_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$GO_README"; then
    pass "$description"
  else
    fail "$description (missing from go/README.md: $needle)"
  fi
}

go_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$GO_README"; then
    fail "$description (found obsolete Go text: $needle)"
  else
    pass "$description"
  fi
}

go_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eq -- "$pattern" "$GO_README"; then
    fail "$description (matched obsolete Go pattern: $pattern)"
  else
    pass "$description"
  fi
}

go_setup_sequence() {
  heading=$1
  block=$(awk -v heading="$heading" '
    $0 == heading { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$GO_README")
  copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r go/ my-project && cd my-project" { print NR; exit }')
  module_line=$(printf '%s\n' "$block" | awk '$0 == "go mod edit -module my-project" { print NR; exit }')
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$copy_line" ] && [ -n "$module_line" ] && [ -n "$init_line" ] &&
     [ -n "$commit_line" ] && [ -n "$workspace_line" ] &&
     [ "$copy_line" -lt "$module_line" ] &&
     [ "$module_line" -lt "$init_line" ] &&
     [ "$init_line" -lt "$commit_line" ] &&
     [ "$commit_line" -lt "$workspace_line" ]; then
    pass "Go $heading customizes the module before clean workspace setup"
  else
    fail "Go $heading must copy, edit the module, initialize, commit, then run make workspace"
  fi
}

go_ci_sequence() {
  block=$(awk '
    $0 == "### Continuous integration" { active = 1; next }
    active && /^(##|###) / { exit }
    active { print }
  ' "$GO_README")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Go CI runs make workspace before make ci'
  else
    fail 'Go CI must run make workspace before make ci'
  fi
}

rust_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$RUST_README"; then
    pass "$description"
  else
    fail "$description (missing from rust/README.md: $needle)"
  fi
}

rust_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$RUST_README"; then
    fail "$description (found obsolete Rust text: $needle)"
  else
    pass "$description"
  fi
}

rust_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eiq -- "$pattern" "$RUST_README"; then
    fail "$description (matched obsolete Rust pattern: $pattern)"
  else
    pass "$description"
  fi
}

rust_setup_sequence() {
  heading=$1
  block=$(awk -v heading="$heading" '
    $0 == heading { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$RUST_README")
  copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r rust/ my-project" { print NR; exit }')
  customize_line=$(printf '%s\n' "$block" | awk '/^# (Edit|Customize) name and description / { print NR; exit }')
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$copy_line" ] && [ -n "$customize_line" ] && [ -n "$init_line" ] &&
     [ -n "$commit_line" ] && [ -n "$workspace_line" ] &&
     [ "$copy_line" -lt "$customize_line" ] &&
     [ "$customize_line" -lt "$init_line" ] &&
     [ "$init_line" -lt "$commit_line" ] &&
     [ "$commit_line" -lt "$workspace_line" ]; then
    pass "Rust $heading copies and customizes before clean workspace setup"
  else
    fail "Rust $heading must copy, customize, initialize, commit, then run make workspace"
  fi
}

rust_ci_sequence() {
  block=$(awk '
    $0 == "### Continuous integration" { active = 1; next }
    active && /^(##|###) / { exit }
    active { print }
  ' "$RUST_README")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Rust CI runs make workspace before make ci'
  else
    fail 'Rust CI must run make workspace before make ci'
  fi
}

monorepo_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$MONOREPO_README"; then
    pass "$description"
  else
    fail "$description (missing from monorepo/README.md: $needle)"
  fi
}

monorepo_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$MONOREPO_README"; then
    fail "$description (found obsolete monorepo text: $needle)"
  else
    pass "$description"
  fi
}

monorepo_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eiq -- "$pattern" "$MONOREPO_README"; then
    fail "$description (matched obsolete monorepo pattern: $pattern)"
  else
    pass "$description"
  fi
}

monorepo_setup_sequence() {
  block=$(awk '
    $0 == "## Getting started" { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$MONOREPO_README")
  root_copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r monorepo/ my-project" { print NR; exit }')
  python_copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r python/ my-project/api" { print NR; exit }')
  bun_copy_line=$(printf '%s\n' "$block" | awk '$0 == "cp -r bun/ my-project/web" { print NR; exit }')
  customize_line=$(printf '%s\n' "$block" | awk '/^# Customize each copied subproject/ { print NR; exit }')
  init_line=$(printf '%s\n' "$block" | awk '$0 == "git init" { print NR; exit }')
  commit_line=$(printf '%s\n' "$block" | awk '/^git add \. && git commit / { print NR; exit }')
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')

  if [ -n "$root_copy_line" ] && [ -n "$python_copy_line" ] && [ -n "$bun_copy_line" ] &&
     [ -n "$customize_line" ] && [ -n "$init_line" ] && [ -n "$commit_line" ] &&
     [ -n "$workspace_line" ] &&
     [ "$root_copy_line" -lt "$python_copy_line" ] &&
     [ "$python_copy_line" -lt "$customize_line" ] &&
     [ "$bun_copy_line" -lt "$customize_line" ] &&
     [ "$customize_line" -lt "$init_line" ] &&
     [ "$init_line" -lt "$commit_line" ] &&
     [ "$commit_line" -lt "$workspace_line" ]; then
    pass 'Monorepo copies root and children, customizes, commits, then runs make workspace'
  else
    fail 'Monorepo must copy root and children, customize, initialize, commit, then run make workspace'
  fi
}

monorepo_ci_sequence() {
  block=$(awk '
    $0 == "## Continuous integration" { active = 1; next }
    active && /^## / { exit }
    active { print }
  ' "$MONOREPO_README")
  workspace_line=$(printf '%s\n' "$block" | awk '$0 == "make workspace" { print NR; exit }')
  ci_line=$(printf '%s\n' "$block" | awk '$0 == "make ci" { print NR; exit }')
  if [ -n "$workspace_line" ] && [ -n "$ci_line" ] && [ "$workspace_line" -lt "$ci_line" ]; then
    pass 'Monorepo CI runs make workspace before make ci'
  else
    fail 'Monorepo CI must run make workspace before make ci'
  fi
}

claude_contains() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$CLAUDE_DOC"; then
    pass "$description"
  else
    fail "$description (missing from CLAUDE.md: $needle)"
  fi
}

claude_rejects() {
  description=$1
  needle=$2
  if grep -Fq -- "$needle" "$CLAUDE_DOC"; then
    fail "$description (found obsolete root instruction: $needle)"
  else
    pass "$description"
  fi
}

claude_rejects_regex() {
  description=$1
  pattern=$2
  if grep -Eiq -- "$pattern" "$CLAUDE_DOC"; then
    fail "$description (matched obsolete root instruction: $pattern)"
  else
    pass "$description"
  fi
}

balanced_fences() {
  description=$1
  file=$2
  fence_count=$(grep -c '^```' "$file" || true)
  if [ $((fence_count % 2)) -eq 0 ]; then
    pass "$description"
  else
    fail "$description (found $fence_count fence markers)"
  fi
}

if [ ! -f "$README" ]; then
  printf 'not ok 1 - README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$PYTHON_README" ]; then
  printf 'not ok 1 - python/README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$PYTHON_CLAUDE_DOC" ]; then
  printf 'not ok 1 - python/CLAUDE.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$PYTHON_AGENTS_DOC" ]; then
  printf 'not ok 1 - python/AGENTS.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$BUN_README" ]; then
  printf 'not ok 1 - bun/README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$BUN_CLAUDE_DOC" ]; then
  printf 'not ok 1 - bun/CLAUDE.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$BUN_AGENTS_DOC" ]; then
  printf 'not ok 1 - bun/AGENTS.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$GO_README" ]; then
  printf 'not ok 1 - go/README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$RUST_README" ]; then
  printf 'not ok 1 - rust/README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$MONOREPO_README" ]; then
  printf 'not ok 1 - monorepo/README.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$CLAUDE_DOC" ]; then
  printf 'not ok 1 - CLAUDE.md is missing\n' >&2
  exit 1
fi
if [ ! -f "$AGENTS_DOC" ]; then
  printf 'not ok 1 - AGENTS.md is missing\n' >&2
  exit 1
fi

if cmp -s "$CLAUDE_DOC" "$AGENTS_DOC"; then
  pass 'Root AGENTS.md is byte-identical to CLAUDE.md'
else
  fail 'Root AGENTS.md must be byte-identical to CLAUDE.md'
fi
if cmp -s "$PYTHON_CLAUDE_DOC" "$PYTHON_AGENTS_DOC"; then
  pass 'Python AGENTS.md is byte-identical to CLAUDE.md'
else
  fail 'Python AGENTS.md must be byte-identical to CLAUDE.md'
fi
python_agents_md_drift_target
if cmp -s "$BUN_CLAUDE_DOC" "$BUN_AGENTS_DOC"; then
  pass 'Bun AGENTS.md is byte-identical to CLAUDE.md'
else
  fail 'Bun AGENTS.md must be byte-identical to CLAUDE.md'
fi
bun_agents_md_drift_target

getting_started=$(awk '
  $0 == "## Getting Started" { active = 1; next }
  active && /^## / { exit }
  active { print }
' "$README")

contains 'workspace is the clean-clone command' '`make workspace` is the one command that turns a clean clone'
contains 'macOS and glibc Linux are supported' 'macOS and glibc Linux'
contains 'x86_64 is supported' '`x86_64`'
contains 'arm64 is supported' '`arm64`'
contains 'base prerequisites include Make through Info-ZIP' 'Make, Bash, Git, curl, tar, Info-ZIP'
contains 'base prerequisites include unzip through writable HOME' 'unzip, a SHA-256 utility, and a writable `HOME`'
contains 'Rust documents the C compiler prerequisite' 'Rust workspaces also require'
contains 'workspace does not use privilege or package-manager setup' 'never uses `sudo` or Homebrew'
contains 'workspace does not edit profiles' 'never edits shell'
contains 'managed tools use the versioned harness directory' '~/.local/share/harness/tools/<tool>/<version>'
contains 'ambient tools are ignored' 'ignores ambient tool versions'
contains 'tracked and index state must be clean' 'clean tracked/index state'
contains 'untracked files are preserved' 'Untracked files'
contains 'preflight happens before downloads' 'Git/Stop-hook collision before'
contains 'hook collisions are preflighted' 'preflights every prerequisite and Git/Stop-hook collision'
contains 'unknown hooks are refused' 'Unknown Git hooks are refused without modification'
contains 'only exact legacy shims migrate' 'legacy harness shims are migrated.'
contains 'locked dependencies are restored' 'restores dependencies from committed'
contains 'embedded skills are deployed' 'deploys the embedded harness skill'
contains 'root Git and Stop hooks are installed' 'installs the root Git and'
contains 'normal auto-fixing check runs' 'normal auto-fixing `make check`'
contains 'tracked changes are reported by path' 'workspace fails and lists'
contains 'offline command is public' 'make workspace OFFLINE=1'
contains 'offline makes no network requests' 'Offline mode makes no network requests'
contains 'offline requires warm caches' 'warm tool and dependency cache'
contains 'cold offline fails before hooks and skills' 'cold or incomplete cache fails before'
contains 'bootstrap is a compatibility alias' '`make bootstrap` remains a compatibility alias'
contains 'deps preserve committed locks' '`make deps` is lock-preserving'
contains 'upgrades stay explicit' 'Dependency upgrades remain explicit native'
contains 'root and monorepo provision a profile union' 'provisions the union of their required profiles'
contains 'subproject deps run once in lexical order' '`make deps` exactly once in lexical order'
contains 'root owns hooks and skills' 'The root owns skill deployment and'
contains 'subproject bootstrap is never called' 'never invokes a subproject bootstrap target'
contains 'CI starts with workspace' 'same contract: `make workspace` followed by `make ci`'

contains 'common pins are exact' 'uv 0.12.5, CPython 3.13.15, lizard 1.22.2'
contains 'Python pins are exact' 'vulture 2.16, pip-audit 2.10.1'
contains 'Bun pins are exact' 'Bun 1.3.14, knip 5.88.1'
contains 'Go runtime and linter pins are exact' 'Go 1.27.0, golangci-lint 2.12.2'
contains 'Go analyzer pins are exact' 'govulncheck 1.1.4, go-arch-lint 1.15.0, gremlins 0.5.0'
contains 'Rust pins are exact' 'rustup 1.28.2, Rust 1.97.1, cargo-audit 0.22.2, cargo-llvm-cov 0.8.7, cargo-modules 0.26.0'

getting_started_sequence '### Python'
getting_started_sequence '### Bun'
getting_started_sequence '### Go'
getting_started_sequence '### Rust'
getting_started_sequence '### Monorepo'

rejects_getting_started 'Python setup does not run uv sync directly' 'uv sync'
rejects_getting_started 'Bun setup does not run bun install directly' 'bun install'
rejects_getting_started 'Go setup does not call runner setup-hooks directly' 'go run harness.go setup-hooks'
rejects_getting_started 'Rust setup does not build and call runner setup-hooks directly' 'cargo build && cargo harness setup-hooks'
rejects_getting_started 'root setup does not install through Homebrew' 'brew install'
rejects 'root guidance does not use uvx' 'uvx'
rejects 'root guidance does not use bunx' 'bunx'
rejects 'root guidance does not claim analyzers are version-on-demand' 'version-on-demand'
rejects 'root guidance does not claim analyzers are installed on demand' 'analyzers on demand'
rejects 'root guidance does not install latest ambient tools' '@latest'
rejects 'bootstrap is not described as native setup plus hooks' 'make bootstrap      # per-language install + root git hook'

if grep -Eiq 'make bootstrap.*(native|install|setup|hook)' "$README"; then
  fail 'bootstrap is documented only as a workspace compatibility alias'
else
  pass 'bootstrap is documented only as a workspace compatibility alias'
fi

python_setup_sequence '## Setup'
python_setup_sequence '## Starting from This Template'
python_contains 'Python links to the root workspace contract' '../README.md#autonomous-workspace'
python_contains 'Python names supported macOS and glibc Linux' 'supported macOS/glibc Linux platforms'
python_contains 'Python names the VM bootstrap prerequisites' 'Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`'
python_contains 'Python ignores ambient runtime tools' 'ignores ambient Python and uv installations'
python_contains 'Python setup requires clean tracked state' 'clean tracked/index state'
python_contains 'Python setup preserves untracked files' 'untracked files are preserved'
python_contains 'Python publishes offline workspace' 'make workspace OFFLINE=1'
python_contains 'Python offline mode makes no network requests' 'Offline mode makes no network requests'
python_contains 'Python offline mode requires warm caches' 'exact tool and dependency caches'
python_contains 'Python bootstrap aliases workspace' '`make bootstrap` is a compatibility alias for `make workspace`'
python_contains 'Python deps are lock preserving' '`make deps` restores the committed dependency graph with `uv sync --locked`'
python_contains 'Python offline deps add the native flag' '`make deps OFFLINE=1` adds `--offline`'
python_contains 'Python upgrades use managed native uv' '.harness/workspace.sh exec python -- uv lock --upgrade'
python_contains 'Python upgrades require lock review' 'git diff -- uv.lock'
python_contains 'Python daily check uses Make' 'make check                 # Fix + format'
python_contains 'Python daily pre-commit uses Make' 'make pre-commit            # Staged checks'
python_contains 'Python daily pre-push uses Make' 'make pre-push              # Read-only push gate'
python_contains 'Python daily CI uses Make' 'make ci                    # Full verification'
python_contains 'Python flagged commands use the managed runner' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness check --verbose'
python_contains 'Python coverage flags use the managed runner' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness coverage --min=80'
python_contains 'Python CRAP flags use the managed runner' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness crap --max=30'
python_contains 'Python enforced CRAP uses the managed runner' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness crap --enforce'
python_contains 'Python suppression updates use the managed runner' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness suppressions --update-baseline'
python_ci_sequence
python_contains 'Python hooks enter the Git root' 'Every Git and Stop hook enters the Git root'
python_contains 'Python hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
python_contains 'Python unknown hooks are refused safely' 'Unknown existing Git hooks cause setup to fail'
python_contains 'Python exact legacy hooks migrate' 'Only exact legacy harness shims are migrated.'

python_rejects_regex 'Python has no direct ambient uv setup command' '^[[:space:]]*uv sync([[:space:]]|$)'
python_rejects_regex 'Python has no direct ambient runner command' '^[[:space:]]*uv run([[:space:]]|$)'
python_rejects 'Python has no ambient uv-run harness form' 'uv run harness'
python_rejects 'Python does not call runner setup-hooks' 'harness setup-hooks'
python_rejects 'Python CI does not call the harness directly' '`harness ci`'
python_rejects 'Python does not use uvx' 'uvx'
python_rejects 'Python does not claim on-demand analyzer installs' 'on-demand'
python_rejects 'Python does not claim analyzers install on demand' 'on demand'
python_rejects_regex 'Python bootstrap is not described as sync plus hooks' 'make bootstrap.*(uv sync|setup-hooks|first-time setup)'

python_claude_contains 'Python agents publish make workspace' '`make workspace` is the clean-clone and new-VM entry point'
python_claude_contains 'Python agents publish warm offline workspace' '`make workspace OFFLINE=1` makes no network requests'
python_claude_contains 'Python agents require warm offline caches' 'warmed the exact tool and dependency caches'
python_claude_contains 'Python agents fail cold offline before hooks and skills' 'offline cache fails before hooks or skills are modified'
python_claude_contains 'Python agents retain the bootstrap alias' '`make bootstrap` is a'
python_claude_contains 'Python agents support macOS and glibc Linux' 'supports macOS and'
python_claude_contains 'Python agents support x86_64' '`x86_64`'
python_claude_contains 'Python agents support arm64' '`arm64`'
python_claude_contains 'Python agents list bootstrap prerequisites' 'Make, Bash, Git, curl,'
python_claude_contains 'Python agents list Info-ZIP and HOME prerequisites' 'Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`'
python_claude_contains 'Python agents require a Git worktree' 'requires a Git'
python_claude_contains 'Python agents require clean tracked state' 'clean tracked/index state'
python_claude_contains 'Python agents preserve untracked files' 'untracked files are preserved'
python_claude_contains 'Python agents prohibit privilege and profile setup' 'does not use `sudo`, Homebrew, or shell'
python_claude_contains 'Python agents use the managed tool path' '~/.local/share/harness/tools/<tool>/<version>'
python_claude_contains 'Python agents pin uv exactly' 'uv 0.12.5'
python_claude_contains 'Python agents pin CPython exactly' 'CPython 3.13.15'
python_claude_contains 'Python agents pin lizard exactly' 'lizard 1.22.2'
python_claude_contains 'Python agents pin vulture exactly' 'vulture 2.16'
python_claude_contains 'Python agents pin pip-audit exactly' 'pip-audit 2.10.1'
python_claude_contains 'Python agents ignore ambient runtimes' 'Ambient Python and tool versions are ignored.'
python_claude_contains 'Python agents preflight both hook destinations' 'both Git-hook destinations before'
python_claude_contains 'Python agents preflight before downloads and mutations' 'downloading or modifying hooks or skills'
python_claude_contains 'Python agents run the auto-fixing check' 'auto-fixing `make check`'
python_claude_contains 'Python agents perform final tracked verification' 'verifies tracked/index state again'
python_claude_contains 'Python agents report changed paths without reverting' 'fails with their paths and does not revert them'
python_claude_contains 'Python agent deps are frozen' '`make deps` restores the committed graph with `uv sync --locked`'
python_claude_contains 'Python agent offline deps use the native offline flag' '`make deps OFFLINE=1` adds `--offline`'
python_claude_contains 'Python agent upgrades use managed uv' '.harness/workspace.sh exec python -- uv lock --upgrade'
python_claude_contains 'Python agent upgrades require lock review' 'git diff -- uv.lock'
python_claude_contains 'Python agent checks are Make-first' 'After edits: `make check`'
python_claude_contains 'Python agent pre-commit is Make-first' 'Pre-commit: `make pre-commit`'
python_claude_contains 'Python agent pre-push is Make-first' 'Pre-push: `make pre-push`'
python_claude_contains 'Python agent CI is Make-first' 'CI: `make ci`'
python_claude_contains 'Python agent setup-hooks is Make-first' 'Setup: `make setup-hooks`'
python_claude_contains 'Python agent advanced runner is frozen and no-sync' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness check --verbose'
python_claude_contains 'Python agent suppression updates use the managed boundary' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness suppressions --update-baseline'
python_claude_contains 'Python agent single test uses the managed boundary' '.harness/workspace.sh exec python -- uv run --frozen --no-sync python -m unittest tests.test_crap'
python_claude_contains 'Python agent lizard diagnostic uses the managed binary' '.harness/workspace.sh exec python -- lizard src tests -C 15 -a 8 -L 100 -i 0'
python_claude_contains 'Python agent vulture diagnostic uses the managed binary' '.harness/workspace.sh exec python -- vulture src vulture_allowlist.py --min-confidence 60'
python_claude_contains 'Python agent hook writes belong only to the provisioner' 'Only `.harness/workspace.sh install-hooks` writes hooks.'
python_claude_contains 'Python agent unknown hooks refuse without modification' 'fail without modifying either hook'
python_claude_contains 'Python agent exact legacy shims migrate' 'only exact legacy harness shims'
python_claude_contains 'Python agent hooks enter the Git root' 'enter the Git root and'
python_claude_contains 'Python agent hooks invoke Make' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
python_claude_ci_sequence
python_claude_contains 'Python agent preserves fail-slow CI output behavior' 'captured and printed in submission order'
python_claude_contains 'Python agent preserves full CI completion behavior' 'run to completion'
python_claude_contains 'Python agent preserves the pre-push gap rationale' 'the offline checks pre-commit and stop-hook skip'
python_claude_contains 'Python agent preserves the tested-dead-helper rationale' 'a dead helper that still has a test surfaces rather than hides'
python_claude_contains 'Python agent preserves advisory CRAP enforcement' "managed runner's \`--enforce\` flag"
python_claude_contains 'Python behavior task sizing remains protected' '<important if="you accept a new task">'
python_claude_contains 'Python human merge authority remains protected' 'The human is the engineer.'
python_claude_contains 'Python architecture guard remains protected' '<important if="you want to edit `.importlinter`'

python_claude_rejects 'Python agents do not use ambient uv-run harness' 'uv run harness'
python_claude_rejects 'Python agents do not use uvx' 'uvx'
python_claude_rejects_regex 'Python agents do not run uv sync directly' '^[[:space:]]*uv sync([[:space:]]|$)'
python_claude_rejects_regex 'Python agents do not run uv directly' '^[[:space:]]*uv run([[:space:]]|$)'
python_claude_rejects 'Python agents do not call runner setup-hooks' 'harness setup-hooks'
python_claude_rejects 'Python agents do not call harness CI directly' '`uv run harness ci`'
python_claude_rejects 'Python agents do not require ambient uv' 'Requires `uv'
python_claude_rejects 'Python agents do not install tools through pip' 'pip install'
python_claude_rejects 'Python agents do not install floating latest tools' '@latest'
python_claude_rejects_regex 'Python bootstrap is not described as native setup' 'make bootstrap.*(uv sync|setup-hooks|first-time setup|install)'

bun_setup_sequence '## Setup'
bun_setup_sequence '## Starting from This Template'
bun_contains 'Bun links to the root workspace contract' '../README.md#autonomous-workspace'
bun_contains 'Bun names supported macOS and glibc Linux' 'supported macOS/glibc Linux platforms'
bun_contains 'Bun names the VM bootstrap prerequisites' 'Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`'
bun_contains 'Bun ignores ambient runtime tools' 'ignores ambient Bun installations'
bun_contains 'Bun setup requires clean tracked state' 'clean tracked/index state'
bun_contains 'Bun setup preserves untracked files' 'untracked files are preserved'
bun_contains 'Bun publishes offline workspace' 'make workspace OFFLINE=1'
bun_contains 'Bun offline mode makes no network requests' 'Offline mode makes no network requests'
bun_contains 'Bun offline mode requires warm caches' 'exact tool and dependency caches'
bun_contains 'Bun bootstrap aliases workspace' '`make bootstrap` is a compatibility alias for `make workspace`'
bun_contains 'Bun deps use the frozen lockfile' '`bun install --frozen-lockfile`'
bun_contains 'Bun offline deps add the native flag' '`make deps OFFLINE=1` adds `--offline`'
bun_contains 'Bun upgrades use managed native Bun' '.harness/workspace.sh exec bun -- bun update'
bun_contains 'Bun upgrades require lock review' 'git diff -- bun.lock'
bun_contains 'Bun daily check uses Make' 'make check                 # Fix + format'
bun_contains 'Bun daily pre-commit uses Make' 'make pre-commit            # Staged checks'
bun_contains 'Bun daily pre-push uses Make' 'make pre-push              # Read-only push gate'
bun_contains 'Bun daily CI uses Make' 'make ci                    # Full verification'
bun_contains 'Bun flagged commands use the managed runner' '.harness/workspace.sh exec bun -- bun harness.ts check --verbose'
bun_contains 'Bun coverage flags use the managed runner' '.harness/workspace.sh exec bun -- bun harness.ts coverage --min=80'
bun_contains 'Bun CRAP flags use the managed runner' '.harness/workspace.sh exec bun -- bun harness.ts crap --max=30'
bun_contains 'Bun enforced CRAP uses the managed runner' '.harness/workspace.sh exec bun -- bun harness.ts crap --enforce'
bun_contains 'Bun suppression updates use the managed runner' '.harness/workspace.sh exec bun -- bun harness.ts suppressions --update-baseline'
bun_contains 'Bun lizard is an exact managed pin' 'exact managed pins for lizard 1.22.2'
bun_contains 'Bun knip is an exact managed pin' 'knip 5.88.1'
bun_ci_sequence
bun_contains 'Bun hooks enter the Git root' 'Every Git and Stop hook enters the Git root'
bun_contains 'Bun hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
bun_contains 'Bun unknown hooks are refused safely' 'Unknown existing Git hooks cause setup to fail'
bun_contains 'Bun exact legacy hooks migrate' 'Only exact legacy harness shims are migrated.'

bun_rejects_regex 'Bun has no direct ambient dependency install command' '^[[:space:]]*bun install([[:space:]]|$)'
bun_rejects_regex 'Bun has no direct ambient package-script command' '^[[:space:]]*bun run([[:space:]]|$)'
bun_rejects_regex 'Bun has no direct ambient harness command' '^[[:space:]]*bun harness\.ts([[:space:]]|$)'
bun_rejects 'Bun does not call runner setup-hooks' 'harness setup-hooks'
bun_rejects 'Bun CI does not call the harness directly' '`bun harness.ts ci`'
bun_rejects 'Bun does not use uvx' 'uvx'
bun_rejects 'Bun does not use bunx' 'bunx'
bun_rejects_regex 'Bun does not require uv' '(^|[^[:alnum:]_])uv([^[:alnum:]_]|$)'
bun_rejects 'Bun does not claim on-demand analyzer installs' 'on-demand'
bun_rejects 'Bun does not claim analyzers install on demand' 'on demand'
bun_rejects_regex 'Bun bootstrap is not described as install plus hooks' 'make bootstrap.*(bun install|setup-hooks|first-time setup)'

bun_claude_contains 'Bun agents publish make workspace' '`make workspace` is the clean-clone and new-VM entry point'
bun_claude_contains 'Bun agents publish warm offline workspace' '`make workspace OFFLINE=1` makes no network requests'
bun_claude_contains 'Bun agents require warm offline caches' 'warmed the exact tool and dependency caches'
bun_claude_contains 'Bun agents fail cold offline before hooks and skills' 'offline cache fails before hooks or skills are modified'
bun_claude_contains 'Bun agents retain the bootstrap alias' '`make bootstrap` is a'
bun_claude_contains 'Bun agents support macOS and glibc Linux' 'supports macOS and'
bun_claude_contains 'Bun agents support x86_64' '`x86_64`'
bun_claude_contains 'Bun agents support arm64' '`arm64`'
bun_claude_contains 'Bun agents list bootstrap prerequisites' 'Make, Bash, Git, curl,'
bun_claude_contains 'Bun agents list Info-ZIP and HOME prerequisites' 'Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`'
bun_claude_contains 'Bun agents require a Git worktree' 'requires a Git'
bun_claude_contains 'Bun agents require clean tracked state' 'clean tracked/index state'
bun_claude_contains 'Bun agents preserve untracked files' 'untracked files are preserved'
bun_claude_contains 'Bun agents prohibit privilege and profile setup' 'does not use `sudo`, Homebrew, or shell'
bun_claude_contains 'Bun agents use the managed tool path' '~/.local/share/harness/tools/<tool>/<version>'
bun_claude_contains 'Bun agents pin Bun exactly' 'Bun 1.3.14'
bun_claude_contains 'Bun agents pin lizard exactly' 'lizard 1.22.2'
bun_claude_contains 'Bun agents pin knip exactly' 'knip 5.88.1'
bun_claude_contains 'Bun agents ignore ambient runtimes' 'Ambient Bun and tool versions are ignored.'
bun_claude_contains 'Bun agents preflight both hook destinations' 'both Git-hook destinations before'
bun_claude_contains 'Bun agents preflight before downloads and mutations' 'downloading or modifying hooks or skills'
bun_claude_contains 'Bun agents run the auto-fixing check' 'auto-fixing `make check`'
bun_claude_contains 'Bun agents perform final tracked verification' 'verifies tracked/index state again'
bun_claude_contains 'Bun agents report changed paths without reverting' 'fails with their paths and does not revert them'
bun_claude_contains 'Bun agent deps are frozen' '`make deps` restores the committed graph with `bun install --frozen-lockfile`'
bun_claude_contains 'Bun agent offline deps use the native offline flag' '`make deps OFFLINE=1` adds `--offline`'
bun_claude_contains 'Bun agent deps never rewrite the lock' 'never upgrades or rewrites `bun.lock`'
bun_claude_contains 'Bun agent upgrades use managed Bun' '.harness/workspace.sh exec bun -- bun update'
bun_claude_contains 'Bun agent upgrades require lock review' 'git diff -- bun.lock'
bun_claude_contains 'Bun agent checks are Make-first' 'After edits: `make check`'
bun_claude_contains 'Bun agent pre-commit is Make-first' 'Pre-commit: `make pre-commit`'
bun_claude_contains 'Bun agent pre-push is Make-first' 'Pre-push: `make pre-push`'
bun_claude_contains 'Bun agent CI is Make-first' 'CI: `make ci`'
bun_claude_contains 'Bun agent setup-hooks is Make-first' 'Setup: `make setup-hooks`'
bun_claude_contains 'Bun agent advanced runner is managed' '.harness/workspace.sh exec bun -- bun harness.ts check --verbose'
bun_claude_contains 'Bun agent suppression updates use the managed boundary' '.harness/workspace.sh exec bun -- bun harness.ts suppressions --update-baseline'
bun_claude_contains 'Bun agent single test uses the managed boundary' '.harness/workspace.sh exec bun -- bun test tests/crap.test.ts'
bun_claude_contains 'Bun agent lizard diagnostic uses the managed binary' '.harness/workspace.sh exec bun -- lizard src tests -C 15 -a 8 -L 100 -i 0'
bun_claude_contains 'Bun agent knip diagnostic uses the managed binary' '.harness/workspace.sh exec bun -- knip --no-config-hints'
bun_claude_contains 'Bun agent hook writes belong only to the provisioner' 'Only `.harness/workspace.sh install-hooks` writes hooks.'
bun_claude_contains 'Bun agent unknown hooks refuse without modification' 'fail without modifying either hook'
bun_claude_contains 'Bun agent exact legacy shims migrate' 'only exact legacy harness shims'
bun_claude_contains 'Bun agent hooks enter the Git root' 'enter the Git root and'
bun_claude_contains 'Bun agent hooks invoke Make' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
bun_claude_ci_sequence
bun_claude_contains 'Bun agent preserves fail-slow CI output behavior' 'captured and printed in submission order'
bun_claude_contains 'Bun agent preserves full CI completion behavior' 'run to completion'
bun_claude_contains 'Bun agent preserves the pre-push gap rationale' 'the offline checks pre-commit and stop-hook skip'
bun_claude_contains 'Bun agent preserves knip configuration guidance' '`knip.json` lists Cucumber step entries'
bun_claude_contains 'Bun agent preserves advisory CRAP enforcement' "managed runner's \`--enforce\` flag"
bun_claude_contains 'Bun behavior task sizing remains protected' '<important if="you accept a new task">'
bun_claude_contains 'Bun human merge authority remains protected' 'The human is the engineer.'
bun_claude_contains 'Bun architecture guard remains protected' '<important if="you want to edit `.dependency-cruiser.json`'

bun_claude_rejects 'Bun agents do not use ambient bun-run commands' 'bun run'
bun_claude_rejects 'Bun agents do not use bunx' 'bunx'
bun_claude_rejects 'Bun agents do not use uvx' 'uvx'
bun_claude_rejects_regex 'Bun agents do not install dependencies directly' '^[[:space:]]*bun install([[:space:]]|$)'
bun_claude_rejects_regex 'Bun agents do not invoke the harness directly' '^[[:space:]]*bun harness\.ts([[:space:]]|$)'
bun_claude_rejects 'Bun agents do not call runner setup-hooks' 'harness.ts setup-hooks'
bun_claude_rejects 'Bun agents do not call harness CI directly' '`bun harness.ts ci`'
bun_claude_rejects 'Bun agents do not require ambient Bun' 'Requires `bun'
bun_claude_rejects 'Bun agents do not install Bun through curl' 'bun.sh/install'
bun_claude_rejects 'Bun agents do not install floating latest tools' '@latest'
bun_claude_rejects_regex 'Bun bootstrap is not described as native setup' 'make bootstrap.*(bun install|setup-hooks|first-time setup|install)'

go_setup_sequence '## Getting Started'
go_setup_sequence '## Starting from This Template'
go_contains 'Go links to the root workspace contract' '../README.md#autonomous-workspace'
go_contains 'Go names supported macOS and glibc Linux' 'supported macOS/glibc Linux platforms'
go_contains 'Go names the VM bootstrap prerequisites' 'Make, Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility'
go_contains 'Go rejects ambient runtime and linter prerequisites' 'No ambient Go, golangci-lint, uv, or'
go_contains 'Go setup requires clean tracked state' 'clean tracked/index state'
go_contains 'Go setup preserves untracked files' 'untracked files are preserved'
go_contains 'Go publishes offline workspace' 'make workspace OFFLINE=1'
go_contains 'Go offline mode makes no network requests' 'Offline mode makes no network requests'
go_contains 'Go offline mode requires warm caches' 'exact tool and dependency caches'
go_contains 'Go bootstrap aliases workspace' '`make bootstrap` is a compatibility alias for `make workspace`'
go_contains 'Go deps use readonly module mode' '`GOFLAGS=-mod=readonly go mod download`'
go_contains 'Go offline deps disable the proxy' 'sets `GOPROXY=off`'
go_contains 'Go upgrades use managed go get' '.harness/workspace.sh exec go -- go get -u ./...'
go_contains 'Go upgrades use managed go mod tidy' '.harness/workspace.sh exec go -- go mod tidy'
go_contains 'Go upgrades require both lock inputs to be reviewed' 'git diff -- go.mod go.sum'
go_contains 'Go runtime pin is exact' 'Go 1.27.0'
go_contains 'Go linter pin is exact' 'golangci-lint 2.12.2'
go_contains 'Go lizard pin is exact' 'lizard 1.22.2'
go_contains 'Go vulnerability analyzer pin is exact' 'govulncheck 1.1.4'
go_contains 'Go architecture analyzer pin is exact' 'go-arch-lint 1.15.0'
go_contains 'Go mutation analyzer pin is exact' 'gremlins 0.5.0'
go_contains 'Go daily check uses Make' '| `make check` | Full pre-flight'
go_contains 'Go daily pre-commit uses Make' '| `make pre-commit` | Staged checks'
go_contains 'Go daily pre-push uses Make' '| `make pre-push` | Read-only push gate'
go_contains 'Go daily CI uses Make' '| `make ci` | Full verification pipeline'
go_contains 'Go flagged commands use the managed runner' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go check --verbose'
go_contains 'Go coverage flags use the managed runner' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go test-cov --min=80'
go_contains 'Go CRAP flags use the managed runner' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go crap --max=30'
go_contains 'Go suppression updates use the managed runner' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go suppressions --update-baseline'
go_contains 'Go mutation package args use the managed runner' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go mutation ./mypkg'
go_ci_sequence
go_contains 'Go hooks enter the Git root' 'Every Git and Stop hook enters the Git root'
go_contains 'Go hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
go_contains 'Go unknown hooks are refused safely' 'Unknown existing Git hooks cause setup to fail'
go_contains 'Go exact legacy hooks migrate' 'Only exact legacy harness shims are migrated.'
go_contains 'Go preserves cold-cache mutation guidance' 'a cold cache makes the first mutant compile'
go_contains 'Go preserves concrete-package mutation guidance' 'gremlins must target a concrete package'

go_rejects_regex 'Go has no direct ambient harness command' '^[[:space:]]*go run harness\.go([[:space:]]|$)'
go_rejects 'Go has no old ambient harness form' 'go run harness.go'
go_rejects 'Go does not call runner setup-hooks' 'harness setup-hooks'
go_rejects 'Go CI does not call the harness directly' '`harness ci`'
go_rejects 'Go does not use uvx' 'uvx'
go_rejects 'Go has no on-demand analyzer claim' 'on-demand'
go_rejects 'Go has no analyzer install-on-demand claim' 'on demand'
go_rejects 'Go has no versioned go-run claim' 'go run ...@version'
go_rejects_regex 'Go has no versioned analyzer execution guidance' 'go run[^`]*@[[:alnum:]]'
go_rejects 'Go has no ambient Go installation URL' 'go.dev/dl'
go_rejects 'Go has no ambient golangci-lint installation URL' 'golangci-lint.run/welcome/install'
go_rejects 'Go has no ambient uv installation URL' 'docs.astral.sh/uv'
go_rejects_regex 'Go does not describe uv as a PATH prerequisite' 'uv.*(PATH|required|prerequisite)'
go_rejects 'Go does not install ambient latest tools' '@latest'
go_rejects_regex 'Go bootstrap is not described as download plus hooks' 'make bootstrap.*(go mod download|setup-hooks|first-time setup)'

rust_setup_sequence '## Setup'
rust_setup_sequence '## Starting from This Template'
rust_contains 'Rust links to the root workspace contract' '../README.md#autonomous-workspace'
rust_contains 'Rust names supported macOS and glibc Linux' 'supported macOS/glibc Linux platforms'
rust_contains 'Rust names the VM bootstrap prerequisites' 'Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, a writable `HOME`, and `cc`'
rust_contains 'Rust ignores ambient runtime tools' 'ignores ambient Rust installations'
rust_contains 'Rust setup requires clean tracked state' 'clean tracked/index state'
rust_contains 'Rust setup preserves untracked files' 'untracked files are preserved'
rust_contains 'Rust publishes offline workspace' 'make workspace OFFLINE=1'
rust_contains 'Rust offline mode makes no network requests' 'Offline mode makes no network requests'
rust_contains 'Rust offline mode requires warm caches' 'exact tool and dependency caches'
rust_contains 'Rust bootstrap aliases workspace' '`make bootstrap` is a compatibility alias for `make workspace`'
rust_contains 'Rust deps fetch the locked graph' '`cargo fetch --locked`'
rust_contains 'Rust deps build the locked graph' '`cargo build --locked`'
rust_contains 'Rust offline deps set Cargo offline' '`CARGO_NET_OFFLINE=true`'
rust_contains 'Rust upgrades use managed Cargo' '.harness/workspace.sh exec rust -- cargo update'
rust_contains 'Rust upgrades require lock review' 'git diff -- Cargo.lock'
rust_contains 'Rust rustup pin is exact' 'rustup 1.28.2'
rust_contains 'Rust compiler pin is exact' 'Rust 1.97.1'
rust_contains 'Rust audit pin is exact' 'cargo-audit'
rust_contains 'Rust audit version is exact' '0.22.2'
rust_contains 'Rust coverage pin is exact' 'cargo-llvm-cov 0.8.7'
rust_contains 'Rust modules pin is exact' 'cargo-modules 0.26.0'
rust_contains 'Rust lizard pin is exact' 'lizard 1.22.2'
rust_contains 'Rust managed toolchain includes llvm tools' '`llvm-tools-preview` for coverage and CRAP'
rust_contains 'Rust manifest intentionally excludes cargo-mutants' '`cargo-mutants` is intentionally absent from the managed manifest'
rust_contains 'Rust mutation reports a deterministic advisory skip' '`Mutation skipped (cargo-mutants is not provisioned)`'
rust_contains 'Rust mutation target documents the skip' 'make mutation                      # deterministic advisory skip'
rust_contains 'Rust daily check uses Make' 'make check                 # Fix + format'
rust_contains 'Rust daily pre-commit uses Make' 'make pre-commit            # Staged checks'
rust_contains 'Rust daily pre-push uses Make' 'make pre-push              # Read-only push gate'
rust_contains 'Rust daily CI uses Make' 'make ci                    # Full verification'
rust_contains 'Rust flagged commands use the managed runner' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- check --verbose'
rust_contains 'Rust coverage flags use the managed runner' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- coverage --min=80'
rust_contains 'Rust CRAP flags use the managed runner' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- crap --max=30'
rust_contains 'Rust enforced CRAP uses the managed runner' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- crap --enforce'
rust_contains 'Rust suppression updates use the managed runner' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- suppressions --update-baseline'
rust_ci_sequence
rust_contains 'Rust hooks enter the Git root' 'Every Git and Stop hook enters the Git root'
rust_contains 'Rust hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
rust_contains 'Rust unknown hooks are refused safely' 'Unknown existing Git hooks cause setup to fail'
rust_contains 'Rust exact legacy hooks migrate' 'Only exact legacy harness shims are migrated.'
rust_contains 'Rust preserves the module-cycle rationale' 'circular dependencies between modules of one crate'
rust_contains 'Rust preserves the orphan-file rationale' 'nor flag orphan source files'
rust_contains 'Rust coverage emits LCOV once' 'emits both the'
rust_contains 'Rust CRAP reuses LCOV without a second test run' 'no second test run'

rust_rejects 'Rust has no ambient cargo install guidance' 'cargo install'
rust_rejects 'Rust does not use uvx' 'uvx'
rust_rejects_regex 'Rust does not require ambient uv' '(^|[^[:alnum:]_])uv([^[:alnum:]_]|$)'
rust_rejects 'Rust has no Homebrew guidance' 'Homebrew'
rust_rejects 'Rust has no brew guidance' 'brew install'
rust_rejects 'Rust has no system LLVM guidance' 'system LLVM'
rust_rejects_regex 'Rust has no LLVM fallback guidance' 'fall(s|ing)? back|fallback'
rust_rejects 'Rust has no distro package fallback' "distro's package"
rust_rejects 'Rust has no ambient cargo-harness form' 'cargo harness'
rust_rejects_regex 'Rust has no direct ambient build command' '^[[:space:]]*cargo build([[:space:]]|$)'
rust_rejects_regex 'Rust has no direct ambient harness command' '^[[:space:]]*cargo harness([[:space:]]|$)'
rust_rejects 'Rust does not call runner setup-hooks' 'harness setup-hooks'
rust_rejects 'Rust CI does not call the harness directly' '`harness ci`'
rust_rejects 'Rust does not mutate the toolchain ambiently' 'rustup component add'
rust_rejects 'Rust does not tell users to install cargo-mutants' 'install cargo-mutants'
rust_rejects_regex 'Rust never claims cargo-mutants is managed' '(provisions|installs|includes).*cargo-mutants'
rust_rejects_regex 'Rust bootstrap is not described as build plus hooks' 'make bootstrap.*(cargo build|setup-hooks|first-time setup)'

monorepo_setup_sequence
monorepo_contains 'Monorepo links to the root workspace contract' '../README.md#autonomous-workspace'
monorepo_contains 'Monorepo names supported macOS and glibc Linux' 'supports macOS'
monorepo_contains 'Monorepo supports x86_64' '`x86_64`'
monorepo_contains 'Monorepo supports arm64' '`arm64`'
monorepo_contains 'Monorepo names root VM prerequisites' 'Make, Bash, Git, curl,'
monorepo_contains 'Monorepo names Info-ZIP and HOME prerequisites' 'Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`'
monorepo_contains 'Monorepo requires cc when Rust is present' 'include `cc` when'
monorepo_contains 'Monorepo rejects ambient toolchain prerequisites' 'No ambient language toolchains are prerequisites.'
monorepo_contains 'Monorepo setup requires clean tracked state' 'clean tracked/index state'
monorepo_contains 'Monorepo setup preserves untracked files' 'preserves untracked'
monorepo_contains 'Monorepo publishes offline workspace' 'make workspace OFFLINE=1'
monorepo_contains 'Monorepo offline mode makes no network requests' 'Offline mode makes no network requests'
monorepo_contains 'Monorepo offline mode requires every warm cache' 'every required tool and dependency cache'
monorepo_contains 'Monorepo bootstrap alias is retained' '`make bootstrap`'
monorepo_contains 'Monorepo setup alias is retained' 'retained `make setup` command'
monorepo_contains 'Monorepo aliases converge through workspace' 'aliases for `make workspace`'
monorepo_contains 'Monorepo discovers and deduplicates markers once' 'discovers and deduplicates top-level project markers once'
monorepo_contains 'Monorepo provisions the profile union once' 'union of required language profiles once'
monorepo_contains 'Monorepo owns skill deployment' 'deploys skills'
monorepo_contains 'Monorepo owns root hooks' 'owns the root Git and Stop hooks'
monorepo_contains 'Monorepo never calls child bootstrap' 'never calls a child bootstrap target'
monorepo_contains 'Monorepo deps run each child once lexically' "calls every detected child's \`make deps\`"
monorepo_contains 'Monorepo deps are lexical' 'once in lexical order'
monorepo_contains 'Monorepo children preserve native locks' 'each child preserves its native lock'
monorepo_contains 'Monorepo Bun runner is exact' '.harness/workspace.sh exec bun -- bun harness.ts <cmd>'
monorepo_contains 'Monorepo Python runner is exact' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness <cmd>'
monorepo_contains 'Monorepo Go runner is exact' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go <cmd>'
monorepo_contains 'Monorepo Rust runner is exact' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- <cmd>'
monorepo_contains 'Monorepo normal and scoped targets use the root provisioner' 'Normal and scoped targets always enter the root provisioner'
monorepo_contains 'Monorepo marker precedence deduplicates directories' 'the first match wins and that directory'
monorepo_contains 'Monorepo quality parallelism remains available' '`PARALLEL=1 make check`'
monorepo_contains 'Monorepo workspace deps remain serial' 'Workspace dependency'
monorepo_contains 'Monorepo quality failures remain fail-slow' '**Fail-slow**'
monorepo_ci_sequence
monorepo_contains 'Monorepo CI has no floating setup' 'There are no floating runtime setup steps to trim.'
monorepo_contains 'Monorepo hooks enter the Git root' 'Every root Git and Stop hook enters the Git root'
monorepo_contains 'Monorepo hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
monorepo_contains 'Monorepo unknown hooks are refused safely' 'Unknown existing Git hooks cause setup to fail'
monorepo_contains 'Monorepo exact legacy hooks migrate' 'Only exact legacy harness shims are migrated.'
monorepo_contains 'Monorepo pre-push captures stdin once' 'pre-push target captures its stdin once'
monorepo_contains 'Monorepo pre-push replays exact input' 'replays the exact input'
monorepo_contains 'Monorepo pre-push replays to every child' 'every child pre-push runner'
monorepo_contains 'Monorepo pre-push replay covers parallel dispatch' 'including parallel'
monorepo_contains 'Monorepo preserves architecture guard behavior' 'Arch config guard'
monorepo_contains 'Monorepo preserves read-only CI behavior' '**`ci` never fixes**'

monorepo_rejects 'Monorepo has no ambient Bun install setup' 'bun install'
monorepo_rejects 'Monorepo has no ambient uv sync setup' 'uv sync'
monorepo_rejects 'Monorepo has no ambient Go module setup' '`go mod download`'
monorepo_rejects 'Monorepo has no ambient Cargo build setup' '`cargo build`'
monorepo_rejects 'Monorepo has no old setup plus hooks bootstrap' '`setup` + `setup-hooks`'
monorepo_rejects 'Monorepo has no per-language native setup description' 'Per-language deps:'
monorepo_rejects 'Monorepo has no one-time install description' 'One-time install + git hook'
monorepo_rejects 'Monorepo has no old Git hook installer prose' 'Install git pre-commit + pre-push hooks'
monorepo_rejects 'Monorepo CI does not run ci without workspace' 'runs `make ci` on every push'
monorepo_rejects 'Monorepo CI has no wired ambient toolchain claim' 'ships with every toolchain wired'
monorepo_rejects 'Monorepo CI does not ask users to trim setup' 'trim the setup steps'
monorepo_rejects 'Monorepo does not use uvx' 'uvx'
monorepo_rejects 'Monorepo does not claim on-demand tools' 'on-demand'
monorepo_rejects 'Monorepo Bun runner is not ambient' '| `harness.ts` | bun | `bun harness.ts'
monorepo_rejects 'Monorepo Python runner is not ambient' '| `harness.py` | python | `uv run harness'
monorepo_rejects 'Monorepo Go runner is not ambient' '| `harness.go` | go | `go run harness.go'
monorepo_rejects 'Monorepo Rust runner is not ambient' '| `Cargo.toml` | rust | `cargo harness'
monorepo_rejects_regex 'Monorepo bootstrap is not described as native setup' 'make bootstrap.*(install|deps|setup-hooks|first-time)'

claude_contains 'Root instructions publish make workspace' '| `make workspace` |'
claude_contains 'Root instructions publish offline workspace' '| `make workspace OFFLINE=1` |'
claude_contains 'Root instructions publish lock-preserving deps' '| `make deps` |'
claude_contains 'Root instructions publish bootstrap and setup aliases' '| `make bootstrap` / `make setup` |'
claude_contains 'Root instructions publish make ci' '| `make ci` |'
claude_contains 'Root instructions publish safe setup-hooks' '| `make setup-hooks` | Delegate collision-safe'
claude_contains 'Root instructions publish root drift target' '| `make agents-md-drift` |'
claude_contains 'Root instructions name macOS and glibc Linux' 'Workspace supports macOS and glibc Linux'
claude_contains 'Root instructions support x86_64' '`x86_64`'
claude_contains 'Root instructions support arm64' '`arm64`'
claude_contains 'Root instructions list bootstrap commands' 'layer is Make, Bash, Git, curl, tar, Info-ZIP unzip'
claude_contains 'Root instructions require writable HOME' 'must be writable; Rust additionally'
claude_contains 'Root instructions require cc for Rust' 'Rust additionally requires `cc`'
claude_contains 'Root instructions require a Git worktree' 'A Git worktree is required.'
claude_contains 'Root instructions require clean tracked and index state' 'Tracked/index state must be clean'
claude_contains 'Root instructions preserve untracked files' 'untracked files are preserved'
claude_contains 'Root instructions prohibit sudo and Homebrew' 'never uses `sudo`, Homebrew'
claude_contains 'Root instructions prohibit profile edits' 'shell-profile edits'
claude_contains 'Root instructions name the managed tool path' '~/.local/share/harness/tools/<tool>/<version>'
claude_contains 'Root instructions ignore ambient versions' 'ambient versions are ignored'
claude_contains 'Root instructions preflight before downloads' 'download or hook/skill mutation'
claude_contains 'Root instructions preflight both hook destinations' 'both Git-hook destinations'
claude_contains 'Root instructions install deps before hooks and skills' 'installs tools and locked dependencies before'
claude_contains 'Root instructions run auto-fixing check' 'auto-fixing `make check`'
claude_contains 'Root instructions report changed tracked paths' 'fails with changed paths'
claude_contains 'Root instructions never revert check changes' 'never reverts those changes'
claude_contains 'Root instructions provision the detected union' 'detected profile union'
claude_contains 'Root instructions dispatch child deps lexically once' 'exactly once in lexical order'
claude_contains 'Root provisioner script is canonical' 'Root `.harness/workspace.sh`'
claude_contains 'Root provisioner lock is canonical' '`.harness/workspace.lock`'
claude_contains 'Root native inputs are copied to all templates' 'all eight native input'
claude_contains 'Root drift test guards provisioner copies' 'bash tests/provisioner_drift_test.sh'
claude_contains 'Root drift covers path bytes and modes' 'complete path set,'
claude_contains 'Root lists Python download input' '`python-downloads.json`'
claude_contains 'Root lists Python tools input' '`python-tools.lock`'
claude_contains 'Root lists Bun package input' '`bun-tools/package.json`'
claude_contains 'Root lists Bun lock input' '`bun-tools/bun.lock`'
claude_contains 'Root lists Go mod input' '`go-tools/go.mod`'
claude_contains 'Root lists Go sum input' '`go-tools/go.sum`'
claude_contains 'Root lists Rust dist input' '`rust-dist.lock`'
claude_contains 'Root lists cargo-modules input' '`cargo-modules.lock`'
claude_contains 'Root template commands are Make-first' 'cd python && make check'
claude_contains 'Root Bun commands are Make-first' 'cd bun    && make check'
claude_contains 'Root Go commands are Make-first' 'cd go     && make check'
claude_contains 'Root Rust commands are Make-first' 'cd rust   && make check'
claude_contains 'Root Python harness boundary is frozen' '.harness/workspace.sh exec python -- uv run --frozen --no-sync harness <command> [args]'
claude_contains 'Root Bun harness boundary is managed' '.harness/workspace.sh exec bun -- bun harness.ts <command> [args]'
claude_contains 'Root Go harness boundary is readonly' '.harness/workspace.sh exec go -- go run -mod=readonly harness.go <command> [args]'
claude_contains 'Root Rust harness boundary is locked' '.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- <command> [args]'
claude_contains 'Root Python single tests use the managed boundary' '.harness/workspace.sh exec python -- uv run --frozen --no-sync python -m unittest tests.test_crap'
claude_contains 'Root Bun single tests use the managed boundary' '.harness/workspace.sh exec bun -- bun test tests/crap.test.ts'
claude_contains 'Root Go single tests use readonly mode' '.harness/workspace.sh exec go -- go test -mod=readonly ./crap/...'
claude_contains 'Root Rust single tests use the locked toolchain' '.harness/workspace.sh exec rust -- cargo test --locked --test smoke'
claude_contains 'Root instructions name direct Python tools' 'Python vulture/pip-audit'
claude_contains 'Root instructions name direct Bun knip' 'Bun knip'
claude_contains 'Root instructions name direct Go analyzers' 'Go govulncheck/go-arch-lint/gremlins'
claude_contains 'Root instructions name direct Rust tools' 'cargo-audit/cargo-llvm-cov/cargo-modules'
claude_contains 'Root runners forbid uvx' 'must not launch tools through `uvx`'
claude_contains 'Root runners forbid bunx' '`bunx`'
claude_contains 'Root runners forbid versioned go-run launchers' 'version-suffixed `go run`'
claude_contains 'Root setup-hooks writer is exclusively the provisioner' 'Only `.harness/workspace.sh install-hooks` writes Git hooks'
claude_contains 'Root runner setup-hooks delegates to the provisioner' "runners' compatibility command delegate to it"
claude_contains 'Root unknown hooks refuse without modification' 'Unknown existing hooks cause preflight refusal without modifying'
claude_contains 'Root exact legacy hooks migrate' 'Only exact legacy harness shims migrate.'
claude_contains 'Root hooks enter the Git root' 'enter the Git root and invoke'
claude_contains 'Root hooks invoke Make targets' '`make pre-commit`, `make pre-push`, or `make stop-hook`'
claude_contains 'Root CI runs workspace then ci' '`make workspace`, then `make ci`'
claude_contains 'Root current skill source remains canonical' '`skills/harness/` is the single source of truth'
claude_contains 'Root behavior task sizing remains protected' '<important if="you accept a new task">'
claude_contains 'Root human merge authority remains protected' 'The human is the engineer.'
claude_contains 'Root architecture guard remains protected' '<important if="you want to edit a template'

claude_rejects_regex 'Root has no direct ambient Python harness command' '^cd python && uv run harness'
claude_rejects_regex 'Root has no direct ambient Bun harness command' '^cd bun[[:space:]]+&& bun (run|harness)'
claude_rejects_regex 'Root has no direct ambient Go harness command' '^cd go[[:space:]]+&& go run harness\.go'
claude_rejects_regex 'Root has no direct ambient Rust harness command' '^cd rust[[:space:]]+&& cargo harness'
claude_rejects 'Root has no cargo-harness runner identity' '`cargo harness`'
claude_rejects 'Root CI does not call harness ci directly' '`harness ci`'
claude_rejects_regex 'Root does not describe lizard as uvx-driven' 'lizard.*(via|through).*uvx'
claude_rejects 'Root has no versioned go-run launcher guidance' 'go run ...@version'
claude_rejects_regex 'Root bootstrap is not described as native setup' 'make bootstrap.*(first-time|uv sync|bun install|go mod download|cargo build|setup-hooks)'
claude_rejects 'Root sync docs do not claim Markdown-only copying' 'skills/harness/*.md'
claude_rejects 'Root does not pre-document embedded skill copies' '.harness/skills/harness'
claude_rejects 'Root does not pre-document generated workspace assets' 'assets/workspace'
claude_rejects 'Root does not pre-document generated template assets' 'assets/templates'

balanced_fences 'root README has balanced Markdown fences' "$README"
balanced_fences 'Python README has balanced Markdown fences' "$PYTHON_README"
balanced_fences 'Bun README has balanced Markdown fences' "$BUN_README"
balanced_fences 'Go README has balanced Markdown fences' "$GO_README"
balanced_fences 'Rust README has balanced Markdown fences' "$RUST_README"
balanced_fences 'Monorepo README has balanced Markdown fences' "$MONOREPO_README"
balanced_fences 'Root CLAUDE.md has balanced Markdown fences' "$CLAUDE_DOC"
balanced_fences 'Python CLAUDE.md has balanced Markdown fences' "$PYTHON_CLAUDE_DOC"
balanced_fences 'Bun CLAUDE.md has balanced Markdown fences' "$BUN_CLAUDE_DOC"

total=$((passed + failed))
printf '1..%d\n' "$total"
if [ "$failed" -ne 0 ]; then
  printf '%d workspace documentation checks failed\n' "$failed" >&2
  exit 1
fi
printf '%d workspace documentation checks passed\n' "$passed"
