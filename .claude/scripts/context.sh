#!/usr/bin/env bash
set -euo pipefail

# context.sh - Gather structured codebase context for agent consumption
# Usage: context.sh [TARGET_DIR] [OPTIONS]

# --- Defaults ---
DEPTH=3
SHOW_TREE=1
SHOW_GIT=1
SHORT_MODE=0

# --- Argument parsing ---
TARGET_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--depth)
            DEPTH="${2:?'--depth requires a number'}"
            shift 2
            ;;
        -s|--short)
            SHORT_MODE=1
            shift
            ;;
        --no-tree)
            SHOW_TREE=0
            shift
            ;;
        --no-git)
            SHOW_GIT=0
            shift
            ;;
        -h|--help)
            cat <<'USAGE'
Usage: context.sh [TARGET_DIR] [OPTIONS]

Arguments:
  TARGET_DIR           Directory to analyze (default: current directory)

Options:
  -d, --depth N        Tree depth limit (default: 3)
  -s, --short          Minimal output: identity + tree only
  --no-tree            Skip file tree section
  --no-git             Skip git status/log sections
  -h, --help           Show usage
USAGE
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            TARGET_DIR="$1"
            shift
            ;;
    esac
done

# --- Resolve target directory ---
TARGET_DIR="${TARGET_DIR:-$PWD}"
if [[ ! -e "$TARGET_DIR" ]]; then
    echo "error: '$TARGET_DIR' does not exist" >&2
    exit 1
fi
if [[ ! -d "$TARGET_DIR" ]]; then
    echo "error: '$TARGET_DIR' is not a directory" >&2
    exit 1
fi
RESOLVED_DIR="$(cd "$TARGET_DIR" 2>/dev/null && pwd -P)" || {
    echo "error: cannot access '$TARGET_DIR' (permission denied?)" >&2
    exit 1
}
TARGET_DIR="$RESOLVED_DIR"
cd "$TARGET_DIR"

# --- Detection helpers ---
IS_GIT=0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    IS_GIT=1
fi

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

# --- Helper functions ---
emit_section() {
    echo ""
    echo "--- $1 ---"
}

truncate_output() {
    local max_lines="$1"
    local label="${2:-entries}"
    local lines=()
    local count=0
    while IFS= read -r line; do
        count=$((count + 1))
        if [[ $count -le $max_lines ]]; then
            lines+=("$line")
        fi
    done
    for l in "${lines[@]}"; do
        echo "$l"
    done
    if [[ $count -gt $max_lines ]]; then
        local remaining=$((count - max_lines))
        echo "... ($remaining more $label)"
    fi
}


# --- Section: TREE ---
section_tree() {
    [[ $SHOW_TREE -eq 0 ]] && return

    emit_section "TREE"

    local ignore_glob=".git|node_modules|__pycache__|.venv|venv|dist|build|.next|.nuxt|target|vendor|*.pyc|.DS_Store"

    if has_cmd eza; then
        eza --tree --level="$DEPTH" \
            --git-ignore \
            --ignore-glob="$ignore_glob" \
            --no-permissions --no-user --no-time --no-filesize \
            "$TARGET_DIR" 2>/dev/null | truncate_output 120 "entries, use -d 2 for less"
    elif has_cmd tree; then
        tree -L "$DEPTH" \
            -I "node_modules|.git|__pycache__|.venv|venv|dist|build|.next|.nuxt|target|vendor" \
            --noreport "$TARGET_DIR" 2>/dev/null | truncate_output 120 "entries"
    else
        # Last resort: find
        find "$TARGET_DIR" -maxdepth "$DEPTH" -type f \
            -not -path '*/.git/*' \
            -not -path '*/node_modules/*' \
            -not -path '*/__pycache__/*' \
            -not -path '*/.venv/*' \
            2>/dev/null | head -100 | sed "s|^$TARGET_DIR/||"
    fi
}

# --- Section: GIT_STATUS ---
section_git_status() {
    [[ $SHOW_GIT -eq 0 || $IS_GIT -eq 0 ]] && return

    emit_section "GIT_STATUS"

    git status --short --branch 2>/dev/null | truncate_output 30 "files"
}

# --- Section: GIT_LOG ---
section_git_log() {
    [[ $SHOW_GIT -eq 0 || $IS_GIT -eq 0 ]] && return

    emit_section "GIT_LOG"

    git log --oneline --no-decorate -n 11 --format='%h %s (%cr)' 2>/dev/null || true
}

# --- Main ---
section_tree

if [[ $SHORT_MODE -eq 0 ]]; then
    section_git_status
    section_git_log
fi

