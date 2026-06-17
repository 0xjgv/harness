# reference-bun

Source: `~/Code/harness-templates/bun/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/bun/CLAUDE.md` verbatim; do not
paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `ci`, `audit`, plus quality
  subcommands `complexity`, `acceptance`, `coverage`, `mutation`, `crap`,
  `arch`, and the drift pair `agents-md-drift` / `sync-agents-md` (keeps
  `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit` fail
  on drift). `ci` pipeline is `lint → typecheck → audit → complexity →
  acceptance → coverage → crap → arch`; `crap` is advisory (warns by
  default, `--enforce` to hard-fail) but still runs in `ci`. `test`,
  `coverage`, `mutation`, and `crap` warn and skip when no Bun test
  files exist. `check` also runs a `hook-drift` check that flags
  `.claude/` hook config drift. Requires `uvx` on PATH for
  `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
  length≤100).
- `## Behavior contract` — Layer 2; see
  [reference-behavior-contract.md](reference-behavior-contract.md).

When adapting an existing repo, keep `bun run <task>` if the repo uses Bun
scripts; otherwise rewrite the prefix to the repo's runner. Keep the
command names and semantics.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/bun/ my-project && cd my-project
bun install && bun run setup-hooks
# Start coding in src/
```

This brings `.claude/` (Layer 2) intact — keep it.

## Hooks

`.claude/settings.json` wires all 5 hooks. Full shape:
[reference-settings-json.md](reference-settings-json.md).
Stop commands:
`cd $CLAUDE_PROJECT_DIR && bun harness.ts post-edit`;
`cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/bun/harness.ts`
- Tooling: Bun runtime, Biome (lint + format), tsc (src + harness + tests), `bun test`,
  `bun audit`, lizard (complexity, via `uvx`), cucumber (acceptance),
  Stryker (mutation), fast-check (property-based tests, see
  `tests/properties.test.ts`), dependency-cruiser (arch)
- Protected arch config: `.dependency-cruiser.json`
