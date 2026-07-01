# reference-settings-json

`.claude/settings.json` and `.codex/hooks.json` wire the harness hooks.
Source files live at:

- `~/Code/harness-templates/<lang>/.claude/settings.json`
- `~/Code/harness-templates/<lang>/.codex/hooks.json`
- `~/Code/harness-templates/<lang>/.codex/hooks/codex-stop-hook.sh`

Copy the pair matching the target language. Claude hooks run inside Claude
Code's hook runtime. Codex hooks run inside Codex's hook runtime and are
trust-gated per project. The contract text in `AGENTS.md`/`CLAUDE.md` (same
content byte-for-byte; the harness `agents-md-drift` check enforces no drift)
applies as instruction to any agent reading the file.

Two cases:

- **Layer 1 only** — Claude and Codex `Stop` hooks run `stop-hook`, which
  auto-formats, then runs complexity (+ deadcode where shipped).
- **Layer 1 + Layer 2** — Claude gets all five hooks (the behavior contract;
  see [reference-behavior-contract.md](reference-behavior-contract.md)).
  Codex still gets the Stop hook through `.codex/hooks.json`.

When merging into existing hook config, add only the hook entries you need and
preserve every other key.

## Claude full shape (Layer 1 + Layer 2)

Every template now ships this. Only the `Stop` commands differ per
language — every other hook is byte-identical.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/scripts/session-start.sh" }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/scripts/ups-classify.sh" }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/scripts/pre-bash-gate.sh" }
        ]
      },
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/scripts/pre-edit-gate.sh" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "<STOP-HOOK COMMAND — see table below>" }
        ]
      }
    ]
  }
}
```

## Claude Stop commands per language

| Template | Stop-hook command |
|---|---|
| Python | `cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook` |
| Bun | `cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook` |
| Go | `cd $CLAUDE_PROJECT_DIR && go run harness.go stop-hook` |
| Rust | `cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook` |
| Monorepo | `cd $CLAUDE_PROJECT_DIR && make stop-hook` |

## Codex Stop hook

Codex project hooks live at `.codex/hooks.json`. Use the repository root from
Git because Codex hook commands run from the session working directory. Codex
parses Stop hook stdout as JSON, so the bundled
`.codex/hooks/codex-stop-hook.sh` wrapper redirects the runner's stdout/stderr
to stderr and prints exactly one JSON object to stdout:

- `{"continue":true}` when checks pass.
- `{"decision":"block","reason":"..."}` when checks fail.

Do not point Codex directly at `make stop-hook` or a language runner that prints
human status lines to stdout.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<CODEX STOP-HOOK COMMAND — see table below>",
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

## Layer 1 only (no behavior contract)

If the user does not want the behavior contract, wire just the Claude `Stop`
block and the Codex `.codex/hooks.json` block above. Drop `SessionStart`,
`UserPromptSubmit`, and `PreToolUse`, and do not copy `.claude/scripts/`.

## Adapting to a different runner

If the repo uses `just`, `make`, or npm scripts instead of the template
runner, only the trailing `Stop` commands change. Keep the Claude
`cd $CLAUDE_PROJECT_DIR &&` prefix, keep the Codex
`cd "$(git rev-parse --show-toplevel)" &&` prefix, and preserve every hook
array shape.
