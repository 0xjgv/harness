# bun

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
  `mutation`, `crap`, `arch`, `arch-config-guard`, `branch-guard`, `suppressions`, and the drift pair `agents-md-drift` /
  `sync-agents-md` (keeps `AGENTS.md` byte-identical to `CLAUDE.md`;
  `check` + `pre-commit` fail on drift). `ci` runs the read-only gates
  (`lint`, `typecheck`, `audit`, `complexity`, `deadcode`, `acceptance`,
  `arch`) **in parallel** — captured and printed in submission order, run to
  completion so one pass surfaces every failure — then streams `coverage` and
  the advisory `crap`; `ci` also runs `arch-config-guard` in strict mode.
  `pre-commit` fixes and formats staged files and typechecks; it no longer
  runs tests. A staged `CLAUDE.md` is copied to `AGENTS.md` and staged with
  it; a hand edit to `AGENTS.md` alone fails the drift check.
  `pre-push` is the offline push gate: after `branch-guard` refuses
  `main`/`master`, it runs the test suite (alone, with git's `GIT_*` hook
  variables stripped, since the suite runs `git init` in temp dirs), then
  `lint` (biome covers format), `acceptance`, `arch`, and strict
  `arch-config-guard` over the whole pushed tree (the deterministic checks
  pre-commit and stop-hook skip).
  `stop-hook` runs post-edit, then lint, complexity, and dead code on the
  change. It prints nothing on success and exits 2 with findings on stderr
  (at most 20 lines). Changed lines come from `git diff -U0` against the
  merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`,
  then `origin/HEAD`, `origin/main`, `origin/master`, `main`, `master`; never
  fetched), plus untracked files. Each gate keeps the targets of its
  whole-tree counterpart: `biome check --reporter=json` on every changed file
  (`error`/`fatal` diagnostics on changed lines; warnings and whole-file
  line-0 diagnostics never block); `lizard --csv` on changed `src/` and
  `tests/` files reports a function over a limit when its lines overlap a
  changed range, so touching an over-limit function blocks and an untouched
  one never does (lizard's TypeScript reader ends a function on the next
  token's line, so the span is cut back to its last `}` line); knip `--reporter codeclimate --include` its symbol issue
  types (unused exports, types, enum/class members, duplicates) runs over the
  project when a `src/` file or `harness.ts` changed and counts on changed
  lines, while unused files and dependencies stay in `ci`. Exit 1 means a
  tool could not run, or the event has `stop_hook_active: true` (the hook
  already blocked this stop). An uncommitted `CLAUDE.md` edit is copied to
  `AGENTS.md`. No arch-config warning and no whole-tree gates at stop.
  `post-edit --hook` (Claude PostToolUse) fixes and formats the one `.ts`
  file of the template the event names and prints one `additionalContext`
  line when it changed. It never blocks.
  `deadcode` runs knip
  (pinned, fetched on demand via `bunx` — no devDep) to flag unused files,
  exports, and dependencies; `knip.json` declares the cucumber step files as
  entries and ignores the tool devDeps invoked as binaries. It runs in `ci`;
  `stop-hook` keeps only symbol findings on changed lines. Concurrent `bunx`
  runs of knip can race while linking their shared install; a stop hook that
  hits this exits 1 and does not block. `crap` is advisory (warns by default, `--enforce` to
  hard-fail) but runs in `ci`, not `stop-hook`. Suppressions are ratcheted by
  `.harness-baseline`; `coverage.min` in the same file is the default coverage floor. `test`, `coverage`, `mutation`, and
  `crap` warn and skip when no Bun test files exist. `check` also warns on
  missing Stop/PostToolUse hook wiring and arch config changes. Requires `uvx`
  on PATH for `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
  length≤100).
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

When adapting an existing repo, keep `bun run <task>` if the repo uses Bun
scripts; otherwise rewrite the prefix to the repo's runner. Keep the
command names and semantics.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/bun/ my-project && cd my-project
bun install && bun run setup-hooks
# Start coding in src/
```

This brings `AGENTS.md`/`CLAUDE.md` (Layer 2), `.claude/settings.json`,
`.codex/hooks.json`, and `.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook (`timeout` 300) and the
Claude PostToolUse hook (`matcher` `Edit|Write`, `timeout` 60);
`.codex/hooks.json` wires the Codex Stop hook only. `setup-hooks` installs the
Stop wiring idempotently; `check` warns when any of the three is missing. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && bun harness.ts stop-hook`.
Claude PostToolUse command:
`cd $CLAUDE_PROJECT_DIR && bun harness.ts post-edit --hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh bun harness.ts stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/bun/harness.ts`
- Tooling: Bun runtime, Biome (lint + format), tsc (src + harness + tests), `bun test`,
  `bun audit`, lizard (complexity, via `uvx`), knip (dead code, via `bunx`),
  cucumber (acceptance), Stryker (mutation), fast-check (property-based tests,
  see `tests/properties.test.ts`), dependency-cruiser (arch)
- Protected arch config: `.dependency-cruiser.json` (`bun harness.ts arch-config-guard`)
- Branch guard: `bun harness.ts branch-guard` (warns nothing, fails on `main`/`master`; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides)
- Dead-code config: `knip.json`
