#!/usr/bin/env bash
# SessionStart hook: reinject the important contract blocks from CLAUDE.md.
set -euo pipefail
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
claude="$dir/CLAUDE.md"
[[ -f "$claude" ]] && awk '/<important/{p=1} p{print} /<\/important>/{p=0; print ""}' "$claude"
exit 0
