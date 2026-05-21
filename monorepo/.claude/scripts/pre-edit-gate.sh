#!/usr/bin/env bash
# PreToolUse(Write|Edit|MultiEdit) hook: deny writes to protected arch config
# paths unless the UserPromptSubmit classifier captured authorization.
set -euo pipefail

STATE_FILE="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/state/edit-auth"
PYTHON3="${PYTHON3:-$(command -v python3 || echo /usr/bin/python3)}"

# Protected arch-config basenames — one per language subproject. The match below
# is suffix-aware (*/<name>), so a config nested in any subproject is covered.
PROTECTED=(".importlinter" ".dependency-cruiser.json" ".go-arch-lint.yml" "arch.toml")

payload=$(cat)
file_path=$(printf '%s' "$payload" | "$PYTHON3" -c 'import json,sys; d=json.load(sys.stdin).get("tool_input",{}); print(d.get("file_path") or d.get("filePath") or "")')

[[ -z "$file_path" ]] && exit 0  # unrelated edit tool — allow

# Normalize to relative path.
rel="${file_path#${CLAUDE_PROJECT_DIR:-$PWD}/}"

is_protected=0
for p in "${PROTECTED[@]}"; do
  if [[ "$rel" == "$p" || "$rel" == *"/$p" ]]; then
    is_protected=1
    break
  fi
done
(( is_protected == 0 )) && exit 0  # not protected — allow

deny() {
  "$PYTHON3" -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$1"
  exit 0
}

if [[ ! -f "$STATE_FILE" ]]; then
  deny "edits to $rel require explicit user authorization in the current prompt (e.g., 'edit .importlinter to ...')"
fi

# Check TTL and path authorization.
now=$(date +%s)
authorized=$("$PYTHON3" -c '
import json,sys
path, state_file, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
  d = json.load(open(state_file))
except Exception:
  print("0"); sys.exit()
if int(d.get("expires_at", 0)) < now:
  print("0"); sys.exit()
paths = d.get("paths", [])
# Monorepo: authorized paths are basenames; rel may be nested (web/.importlinter).
ok = any(path == p or path.endswith("/" + p) for p in paths)
print("1" if ok else "0")
' "$rel" "$STATE_FILE" "$now")

if [[ "$authorized" != "1" ]]; then
  deny "edits to $rel require the user's current prompt to pair a verb (edit/update/modify/...) with the path name"
fi

exit 0
