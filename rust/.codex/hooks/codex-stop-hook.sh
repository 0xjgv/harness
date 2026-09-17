#!/bin/sh
# Codex Stop hook wrapper. Codex reads one JSON object from stdout, so this maps
# the harness stop-hook contract onto it:
#   exit 0, stdout is a JSON object -> pass it through (dispatchers answer in JSON)
#   exit 0                          -> {"continue":true}
#   exit 2                          -> block; the reason is the stderr findings
#   any other exit (tool failure)   -> {"continue":true}; stderr stays visible
set -u

if [ "$#" -eq 0 ]; then
  printf '%s\n' '{"decision":"block","reason":"Codex stop-hook wrapper received no command to run."}'
  exit 0
fi

tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT INT TERM

"$@" >"$tmp/out" 2>"$tmp/err"
rc=$?
cat "$tmp/err" >&2

json_escape() {
  awk 'BEGIN { ORS = "" }
    {
      gsub(/\033\[[0-9;]*[A-Za-z]/, "")
      gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); gsub(/\t/, "\\t")
      gsub(/[\001-\010\013-\037]/, "")
      if (NR > 1) print "\\n"
      print
    }'
}

if [ "$rc" -eq 0 ]; then
  if sed -n '/[^[:space:]]/{p;q;}' "$tmp/out" | grep -q '^[[:space:]]*{'; then
    cat "$tmp/out"
  else
    cat "$tmp/out" >&2
    printf '%s\n' '{"continue":true}'
  fi
  exit 0
fi

cat "$tmp/out" >&2
if [ "$rc" -ne 2 ]; then
  printf '%s\n' '{"continue":true}'
  exit 0
fi

reason=$(tail -n 22 "$tmp/err" | json_escape)
[ -n "$reason" ] || reason="Stop hook checks failed; run the stop-hook command to see why."
printf '{"decision":"block","reason":"%s"}\n' "$reason"
