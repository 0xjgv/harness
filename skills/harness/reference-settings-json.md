# reference-settings-json

`.claude/settings.json` wires the harness hooks. Source files live at
`~/Code/harness-templates/<lang>/.claude/settings.json` — copy the one
matching the target language. These hooks run inside Claude Code's hook
runtime. The contract text in `AGENTS.md`/`CLAUDE.md` (same content
byte-for-byte; the harness `agents-md-drift` check enforces no drift)
applies as instruction to any agent reading the file.

Two cases:

- **Layer 1 only** — just the `Stop` hook (post-edit auto-format, then
  stop-hook complexity).
- **Layer 1 + Layer 2** — all five hooks (the behavior contract; see
  [reference-behavior-contract.md](reference-behavior-contract.md)).

When merging into an existing `.claude/settings.json`, add only the hook
entries you need and preserve every other key.

## Full shape (Layer 1 + Layer 2)

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
          { "type": "command", "command": "<POST-EDIT COMMAND — see table below>" },
          { "type": "command", "command": "<STOP-HOOK COMMAND — see table below>" }
        ]
      }
    ]
  }
}
```

## Stop commands per language

| Template | Post-edit command | Stop-hook command |
|---|---|---|
| Python | `cd $CLAUDE_PROJECT_DIR && uv run harness post-edit` | `cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook` |
| Bun | `cd $CLAUDE_PROJECT_DIR && bun harness.ts post-edit` | `cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook` |
| Go | `cd $CLAUDE_PROJECT_DIR && go run harness.go post-edit` | `cd $CLAUDE_PROJECT_DIR && go run harness.go stop-hook` |
| Rust | `cd $CLAUDE_PROJECT_DIR && cargo harness post-edit` | `cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook` |
| Monorepo | `cd $CLAUDE_PROJECT_DIR && make post-edit` | `cd $CLAUDE_PROJECT_DIR && make stop-hook` |

## Layer 1 only (no behavior contract)

If the user does not want the behavior contract, wire just the `Stop`
block above — drop `SessionStart`, `UserPromptSubmit`, and `PreToolUse`,
and do not copy `.claude/scripts/`.

## Adapting to a different runner

If the repo uses `just`, `make`, or npm scripts instead of the template
runner, only the trailing `Stop` commands change. Keep the
`cd $CLAUDE_PROJECT_DIR &&` prefix and every hook array shape.
