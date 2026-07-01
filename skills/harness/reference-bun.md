# reference-bun

Source: `~/Code/harness-templates/bun/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/bun/CLAUDE.md` verbatim; do not
paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `pre-push`, `ci`, `audit`, plus
  quality subcommands `complexity`, `deadcode`, `acceptance`, `coverage`,
  `mutation`, `crap`, `arch`, `suppressions`, and the drift pair `agents-md-drift` /
  `sync-agents-md` (keeps `AGENTS.md` byte-identical to `CLAUDE.md`;
  `check` + `pre-commit` fail on drift). `ci` runs the read-only gates
  (`lint`, `typecheck`, `audit`, `complexity`, `deadcode`, `acceptance`,
  `arch`) **in parallel** — captured and printed in submission order, run to
  completion so one pass surfaces every failure — then streams `coverage` and
  the advisory `crap`. `pre-push` is the offline push gate: `lint` (biome
  covers format), `acceptance`, `arch` over the whole pushed tree (the
  deterministic checks pre-commit and stop-hook skip). `deadcode` runs knip
  (pinned, fetched on demand via `bunx` — no devDep) to flag unused files,
  exports, and dependencies; `knip.json` declares the cucumber step files as
  entries and ignores the tool devDeps invoked as binaries. It runs in `ci`
  and `stop-hook`. `crap` is advisory (warns by default, `--enforce` to
  hard-fail) but runs in `ci`, not `stop-hook`. Suppressions are ratcheted by
  `.harness-baseline`; `coverage.min` in the same file is the default coverage floor. `test`, `coverage`, `mutation`, and
  `crap` warn and skip when no Bun test files exist. `check` also runs a
  `hook-drift` check that flags `.claude/` hook config drift. Requires `uvx`
  on PATH for `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
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

This brings `.claude/` (Layer 2), `.codex/hooks.json`, and
`.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires Claude hooks; `.codex/hooks.json` wires the
Codex Stop hook. Full shape:
[reference-settings-json.md](reference-settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/bun/harness.ts`
- Tooling: Bun runtime, Biome (lint + format), tsc (src + harness + tests), `bun test`,
  `bun audit`, lizard (complexity, via `uvx`), knip (dead code, via `bunx`),
  cucumber (acceptance), Stryker (mutation), fast-check (property-based tests,
  see `tests/properties.test.ts`), dependency-cruiser (arch)
- Protected arch config: `.dependency-cruiser.json`
- Dead-code config: `knip.json`
