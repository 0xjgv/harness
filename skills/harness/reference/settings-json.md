# settings-json

`.claude/settings.json` and `.codex/hooks.json` wire the agent Stop hooks.
Source files live at:

- `~/Code/harness-templates/<lang>/.claude/settings.json`
- `~/Code/harness-templates/<lang>/.codex/hooks.json`
- `~/Code/harness-templates/<lang>/.codex/hooks/codex-stop-hook.sh`

Copy the pair matching the target language. Claude hooks run inside Claude
Code's hook runtime. Codex hooks run inside Codex's hook runtime and are
trust-gated per project. The contract text in `AGENTS.md`/`CLAUDE.md` applies
as instruction to any agent reading the file.

The templates wire only `Stop` hooks. The Stop hook runs `stop-hook`, which
formats changed files, then runs complexity plus deadcode where the language
ships a separate deadcode gate.

## Claude Stop hook

Every template ships this shape. Only the command differs per language.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "<STOP-HOOK COMMAND - see table below>" }
        ]
      }
    ]
  }
}
```

| Template | Stop-hook command |
|---|---|
| Python | `cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook \|\| exit 2` |
| Bun | `cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook \|\| exit 2` |
| Go | `cd $CLAUDE_PROJECT_DIR && go run harness.go stop-hook \|\| exit 2` |
| Rust | `cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook \|\| exit 2` |
| Monorepo | `cd $CLAUDE_PROJECT_DIR && make stop-hook \|\| exit 2` |

Every Claude Stop-hook command ends in `|| exit 2`. Claude Code only treats a
Stop hook's exit code **2** as blocking — it feeds the hook's stderr back to
the model so the agent must address the failure before stopping. Any other
non-zero exit is a non-blocking error the model never sees, so a runner that
fails with exit 1 (or anything but 2) stops silently. Each runner's
`stop-hook` subcommand already exits 2 on failure internally, but that exit
code does not always survive the wrapper Claude actually invokes: `go run`
collapses a child process's exit code and prints `exit status N` to stderr,
then exits 1 itself, no matter what code the compiled program returned. `||
exit 2` re-asserts the blocking exit code after the wrapper, at the shell
level, regardless of what the underlying runner/toolchain does to the code in
between. For runners where propagation already works (Python, Bun, Rust,
`make`) the suffix is a no-op on the success and failure paths alike — it
costs nothing and keeps all templates identical, which also makes the
contract robust if a toolchain's exit-code propagation changes later.

## Codex Stop hook

Codex project hooks live at `.codex/hooks.json`. Use the repository root from
Git because Codex hook commands run from the session working directory. Codex
parses Stop hook stdout as JSON, so the bundled
`.codex/hooks/codex-stop-hook.sh` wrapper redirects the runner's stdout/stderr
to stderr and prints exactly one JSON object to stdout:

- `{"continue":true}` when checks pass.
- `{"decision":"block","reason":"..."}` when checks fail.

Do not point Codex directly at `make stop-hook` or a language runner that
prints human status lines to stdout.

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
| Go | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh go run harness.go stop-hook` |
| Rust | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh cargo harness stop-hook` |
| Monorepo | `cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook` |

## Adapting to a different runner

If the repo uses `just`, `make`, or npm scripts instead of the template
runner, only the trailing `Stop` commands change. Keep the Claude
`cd $CLAUDE_PROJECT_DIR &&` prefix, keep the Codex
`cd "$(git rev-parse --show-toplevel)" &&` prefix, and preserve every hook
array shape.

Do not add SessionStart, UserPromptSubmit, or PreToolUse behavior gates. The
current behavior contract is enforced through instructions plus
`arch-config-guard` in the runner.
