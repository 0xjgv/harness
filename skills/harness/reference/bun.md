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
  `mutation`, `crap`, `arch`, `arch-config-guard`, `gherkin-guard`,
  `suppressions`, and the drift pair `agents-md-drift` /
  `sync-agents-md` (keeps `AGENTS.md` byte-identical to `CLAUDE.md`;
  `check` + `pre-commit` fail on drift). `check` runs a lockfile check
  (`bun install --frozen-lockfile`), fix + format, typecheck, and test
  (warns/skips when no tests exist), then — as a read-only parallel batch —
  complexity, deadcode, acceptance (self-skips with a warning when no
  `.feature` files exist), and `arch` (dependency-cruiser qualifies for
  `check`'s batch: it's a local devDependency, runs offline, and takes no
  build lock), then warns (does not block) via `arch-config-guard`
  and `gherkin-guard`, checks Stop-hook wiring and `agents-md-drift`, and
  ratchets suppressions. Invariant: `ci` minus `check` == every gate that
  needs the network or a build lock (`audit`, `coverage`, advisory `crap`).
  `ci` runs the read-only gates
  (`lint`, `typecheck`, `audit`, `complexity`, `deadcode`, `acceptance`,
  `arch`) **in parallel** — captured and printed in submission order, run to
  completion so one pass surfaces every failure — then streams `coverage` and
  the advisory `crap`; `ci` also runs `arch-config-guard` and `gherkin-guard`
  in strict (blocking) mode.
  `pre-push` is the offline push gate: `lint` (biome
  covers format), `acceptance`, `arch`, and strict `arch-config-guard` + `gherkin-guard` over the whole pushed tree (the
  deterministic checks pre-commit and stop-hook skip). `deadcode` runs knip
  (pinned, fetched on demand via `bunx` — no devDep) to flag unused files,
  exports, and dependencies; `knip.json` declares the cucumber step files as
  entries and ignores the tool devDeps invoked as binaries. It runs in `check`,
  `ci`, and `stop-hook`. `gherkin-guard` blocks a changed `src/` file with no
  changed `.feature` in `pre-commit`/`pre-push`/`ci`
  (`HARNESS_ALLOW_NO_FEATURE=1` overrides after review); it only warns in
  `check`/`stop-hook`, and skips silently when the repo has no `.feature`
  files anywhere. `crap` is advisory (warns by default, `--enforce` to
  hard-fail) but runs in `ci`, not `stop-hook` or `check`. `.harness-baseline` is
  a merge-based ratchet: `suppressions --update-baseline` re-measures `coverage.min`,
  `complexity.max_violations`, `crap.max_violations` and every `suppressions.<kind>`
  (a vanished kind is written as 0), drops a key it cannot measure in this repo
  (with a warning), preserves keys it does not own (`mutation.min`), and writes
  nothing if any measurement errors. `complexity` passes the floor to lizard as
  `-i N`; `crap` compares its offender count to the floor. A missing file or key
  makes both gates report-only: they pass — `crap` under `--enforce` too —
  labelled `report-only: no .harness-baseline floor`, with a hint to run
  `bun harness.ts suppressions --update-baseline`. `test`, `coverage`, `mutation`, and
  `crap` warn and skip when no Bun test files exist. `check` also warns on
  missing Stop hook wiring and arch config changes. Requires `uvx`
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

`.claude/settings.json` wires the Claude Stop hook; `.codex/hooks.json` wires
the Codex Stop hook. Full shape:
[settings-json.md](settings-json.md).
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
- Protected arch config: `.dependency-cruiser.json` (`bun harness.ts arch-config-guard`)
- Dead-code config: `knip.json`
