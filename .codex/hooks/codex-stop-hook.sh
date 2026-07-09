#!/bin/sh
set -u

if [ "$#" -eq 0 ]; then
  printf '%s\n' '{"decision":"block","reason":"Codex stop-hook wrapper received no command to run."}'
  exit 0
fi

if "$@" >&2; then
  printf '%s\n' '{"continue":true}'
else
  printf '%s\n' '{"decision":"block","reason":"Stop hook checks failed; review the output above and fix it before stopping."}'
fi
