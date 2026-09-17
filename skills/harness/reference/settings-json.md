# settings-json

`.claude/settings.json` and `.codex/hooks.json` wire the agent hooks.
Source files live at:

- `~/Code/harness-templates/<lang>/.claude/settings.json`
- `~/Code/harness-templates/<lang>/.codex/hooks.json`
- `~/Code/harness-templates/<lang>/.codex/hooks/codex-stop-hook.sh`

Copy the pair matching the target language. Claude hooks run inside Claude
Code's hook runtime. Codex hooks run inside Codex's hook runtime and are
trust-gated per project. The contract text in `AGENTS.md`/`CLAUDE.md` applies
as instruction to any agent reading the file.

Every template wires two Claude hooks and one Codex hook:

- **Stop** (Claude and Codex) runs `stop-hook`: fix and format changed files,
  then gate only what the change introduced.
- **PostToolUse** on `Edit|Write` (Claude only) runs `post-edit --hook`: fix
  and format the one file just written.

## The `stop-hook` contract

The Stop hook is a feedback loop for the agent, not a report on the
repository. Pre-existing debt never blocks a stop; `check` and `ci` keep the
whole-tree gates.

| Check at stop | Scope | Result |
|---|---|---|
| fix + format (`post-edit`) | changed files | never blocks |
| lint left after the fix | changed lines | blocks |
| complexity (lizard) | functions new, or worse than at the merge-base | blocks |
| dead code (python: vulture; bun: knip exports) | changed lines | blocks |
| arch config, whole-tree gates, tests | — | not run at stop |

"Changed" is measured against the merge-base with the default branch
(`origin/HEAD`, then `origin/main`/`master`, then local `main`/`master`), plus
uncommitted and untracked files. With no base ref it is `HEAD`; the hook never
fetches.

Exit codes and output:

- **0**, nothing printed: the change is clean.
- **2**, findings on stderr, nothing on stdout. Claude Code blocks the stop and
  feeds stderr to the model, so the payload is written for a model:

  ```text
  stop-hook failed: Lint, Complexity
  src/app/core.py:42: F841 Local variable `x` is assigned to but never used
  src/app/core.py:10: parse CCN 14→17 (limit 15)
  … +3 more — run `uv run harness stop-hook --verbose`
  ```

  Gates are named `Lint`, `Complexity`, `Dead code`. At most 20 finding
  lines, each `path:line: message`; `--verbose` lifts the cap.
- **1**, one stderr line: a tool could not run (missing binary, crash,
  unparsable output). Claude Code shows it to the human and does not block. A
  tool failure never exits 2.

The Stop command must preserve exit code 2. `go run` reports any failing
program as exit 1, so the Go template builds `./harness` (gitignored) and runs
the binary; `uv run`, `bun`, and `cargo run` pass the code through.

Loop guard: the hook reads the event from stdin. When `stop_hook_active` is
true and the findings are byte-identical to the previous stop's, it prints
them with `harness: same findings as the previous stop; not blocking again`
and exits 1. Findings that changed (one of three fixed) block again. The
previous payload's hash lives under `$(git rev-parse --git-path harness)/`.

## Claude hooks

Every template ships this shape. Only the commands differ per language.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<STOP-HOOK COMMAND - see table below>",
            "timeout": 300
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "<POST-EDIT COMMAND - see table below>",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

| Template | Stop command | PostToolUse command |
|---|---|---|
| Python | `cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook` | `cd $CLAUDE_PROJECT_DIR && uv run harness post-edit --hook` |
| Bun | `cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook` | `cd $CLAUDE_PROJECT_DIR && bun harness.ts post-edit --hook` |
| Go | `cd $CLAUDE_PROJECT_DIR && go build -o harness harness.go && ./harness stop-hook` | `cd $CLAUDE_PROJECT_DIR && go run harness.go post-edit --hook` |
| Rust | `cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook` | `cd $CLAUDE_PROJECT_DIR && cargo harness post-edit --hook` |
| Monorepo | `cd $CLAUDE_PROJECT_DIR && make -s stop-hook` | `cd $CLAUDE_PROJECT_DIR && make -s post-edit-hook` |

### PostToolUse

A Stop hook arrives a whole turn after the edit. PostToolUse arrives right
after each `Edit`/`Write`, while the agent still has the file in mind, so the
formatter runs there too and a stop-time reformat stops invalidating the
agent's last read.

`post-edit --hook` reads the event on stdin and takes
`tool_input.file_path`. It acts only on a source file of that template; any
other file, or unparsable input, is a silent no-op. It fixes and formats the
whole file and **always exits 0**:

- nothing on stdout when the file did not change;
- when the file changed, exactly one line:
  `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"harness: reformatted <path>; re-read it before editing it again"}}`

It never blocks. Lint the fixer could not resolve comes back at stop.

Codex has a PostToolUse event too, but its edit tool sends a patch rather than
a file path, so `.codex/hooks.json` keeps only the Stop wiring.

## Monorepo dispatch

`make` turns every failed recipe into exit 2, so an exit code cannot carry
the 0/1/2 contract through a Makefile. The monorepo (and this repo's root)
`stop-hook` therefore runs each dirty subproject's `stop-hook` with the event
on stdin and always exits 0, answering with one JSON object on stdout:

- `{"continue":true}` when every subproject is clean;
- `{"decision":"block","reason":"<findings>"}` when any subproject exited 2,
  with paths prefixed by the subproject directory;
- a `"systemMessage"` with the stderr of any subproject whose tool failed.

Claude Code and Codex both read this shape. Run from a terminal (stdin is a
TTY), it prints the findings instead and exits 1 when there are any.

`make -s post-edit-hook` forwards the PostToolUse event to the subproject that
owns `tool_input.file_path` (an absolute path, as Claude Code sends it).

## Codex Stop hook

Codex project hooks live at `.codex/hooks.json`. Use the repository root from
Git because Codex hook commands run from the session working directory. Codex
parses Stop hook stdout as JSON, so the bundled
`.codex/hooks/codex-stop-hook.sh` wrapper sends the runner's output to stderr
and prints exactly one JSON object to stdout:

- exit 0 → `{"continue":true}`, or the runner's own stdout when it is already
  a JSON object (the monorepo dispatch);
- exit 2 → `{"decision":"block","reason":"<last 22 stderr lines>"}`;
- any other exit → `{"continue":true}`: a tool failure is shown, not blocked.

Do not point Codex directly at a language runner that prints human status
lines to stdout.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<CODEX STOP-HOOK COMMAND - see table below>",
            "timeout": 300,
            "statusMessage": "Running stop-hook checks"
          }
        ]
      }
    ]
  }
}
```

| Template | Codex Stop-hook command |
|---|---|
| Python | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh uv run harness stop-hook` |
| Bun | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook` |
| Go | `cd "$(git rev-parse --show-toplevel)" && go build -o harness harness.go && .codex/hooks/codex-stop-hook.sh ./harness stop-hook` |
| Rust | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh cargo harness stop-hook` |
| Monorepo | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make -s stop-hook` |

## Adapting to a different runner

If the repo uses `just`, `make`, or npm scripts instead of the template
runner, only the trailing commands change. Keep the Claude
`cd $CLAUDE_PROJECT_DIR &&` prefix, keep the Codex
`cd "$(git rev-parse --show-toplevel)" &&` prefix, and preserve every hook
array shape. A runner that goes through `make` must answer in JSON like the
monorepo dispatch, because `make` flattens exit codes.

Do not add SessionStart, UserPromptSubmit, or PreToolUse behavior gates. The
current behavior contract is enforced through instructions plus
`arch-config-guard` and `branch-guard` in the runner. PostToolUse is not a
behavior gate: it formats the file that was just written and never blocks.
