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
  (scoped to the change set), then — as a read-only parallel batch —
  complexity (CCN + duplicate blocks), deadcode, acceptance (self-skips with a warning when no
  `.feature` files exist), and `arch` (dependency-cruiser qualifies for
  `check`'s batch: it's a local devDependency, runs offline, and takes no
  build lock), then warns (does not block) via `arch-config-guard`
  and `gherkin-guard`, checks Stop-hook wiring and `agents-md-drift`, and
  ratchets suppressions. Invariant: `ci` minus `check` == every gate that
  needs the network or a build lock, or is too slow for an edit loop (`audit`,
  `coverage`, advisory `crap`, advisory `mutation`) — plus the tests outside the
  change set, which `ci` reaches through coverage and `check` deliberately skips
  (`--all` runs them locally).
  `ci` runs the read-only gates
  (`lint`, `typecheck`, `audit`, `complexity` (CCN + duplicate blocks),
  `deadcode`, `acceptance`, `arch`) **in parallel** — captured and printed in
  submission order, run to completion so one pass surfaces every failure — then
  streams `coverage`, the advisory `crap`, and the advisory `mutation` (scoped to
  the diff against the resolved base ref, never the working tree, which is empty
  on a CI checkout); `ci` also runs `arch-config-guard` and `gherkin-guard` in
  strict (blocking) mode.
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
  `complexity.max_violations`, `duplication.max_blocks`, `crap.max_violations`, `arch.max_violations` and every
  `suppressions.<kind>`
  (a vanished kind is written as 0), drops a key it cannot measure in this repo
  (with a warning), preserves keys it does not measure, and writes
  nothing if any measurement errors. `complexity` passes the floor to lizard as
  `-i N`; `crap` compares its offender count to the floor; `arch` runs
  dependency-cruiser with `--output-type json`, counts
  `summary.error + summary.warn` (never `summary.violations.length`, which also
  counts `info`/`ignore` findings), ignores dependency-cruiser's exit code, and
  fails only above the floor; `mutation` compares its score to `mutation.min`.
  A missing file or key makes all four gates
  report-only: they pass — `crap` and `mutation` under `--enforce` too —
  labelled `report-only: no .harness-baseline floor`, with a hint to run
  `bun harness.ts suppressions --update-baseline`, so a repo adopting the harness
  with pre-existing boundary violations is green on day one and can only ratchet
  down from there. `complexity` also runs
  lizard a second time with `-Eduplicate -w` over the same targets — lizard's
  exit code only ever reflects CCN warnings, so the runner counts
  `Duplicate block:` headers itself and compares them to `duplication.max_blocks`
  (report-only when absent, the same way). lizard reports a block only once it
  spans ~70 unified tokens and counts overlapping near-duplicates separately, so
  the count can move by more than one when unrelated code changes in the same
  file (observed in this template: 2 → 0 when a test file grew by ~50 lines that
  touched none of the duplicated regions). Treat it as a floor to hold, never a
  threshold to hit. `mutation.min` is the one
  floor that pass does *not* measure — a Stryker run costs minutes, so it is
  carried through untouched unless you add `--with-mutation` — so a repo that
  copied this template must either delete the shipped `mutation.min` line or
  re-measure it with `--with-mutation`, or it keeps the template's number. Its unit is
  `round(100 * killed / (killed + survived))` over the statuses in Stryker's
  JSON report (`Killed` + `Timeout` against `Survived`; mutants that never ran
  are excluded from both sides, and Stryker's own `thresholds.break` is never
  used because its denominator differs). `test`, `coverage`, `mutation`, and
  `crap` warn and skip when no Bun test files exist. `check` and `pre-commit`
  scope `test` to the change set — the tests that reach a changed file through
  imports, run via `bun test --changed` where the runtime advertises it. The
  change set is the same union the guards use (working tree + index + untracked
  + `<base>...HEAD`, base from `--base=<ref>`/`HARNESS_ARCH_BASE`/
  `GITHUB_BASE_REF`/fallback refs). An empty change set warns and skips instead
  of widening; a changed source no test imports warns once and never fails.
  `--all` and `ci` (through coverage) run the whole suite; `pre-push` has no
  test gate, so `--all` is the local way to run everything. `check` also warns on
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

## Pinned versions

Every input that decides a gate's *verdict* is pinned, so the same tree gates
the same way on any machine and in CI:

- **Tool devDependencies** are exact, not ranged: `@biomejs/biome`,
  `@stryker-mutator/core`, `dependency-cruiser`, and `typescript` carry bare
  versions in `package.json`, and `bun.lock` is committed. `check` runs
  `bun install --frozen-lockfile` first, so a hand-edited manifest fails there.
  `@types/bun`, `@cucumber/cucumber`, and `fast-check` stay ranged; the committed
  lock still pins what actually installs, so only a deliberate `bun update` moves
  them.
- **Tools fetched on demand** carry the version in the runner: `lizard@1.22.2`
  (`LIZARD`) and `knip@5.88.1` (`KNIP`).
- **The bun runtime** is pinned by `package.json`'s `packageManager` field
  (`bun@1.4.1`), and `bun-version` under `oven-sh/setup-bun@v2` in
  `.github/workflows/ci.yml` is set to the same value. The runner compares
  `Bun.version` to `packageManager` at startup and prints a green `⚠` on drift;
  it never fails, because an adopting repo may legitimately run another bun.
  `--verbose` also prints the match. `packageManager` is read in the plain form
  and in corepack's `bun@1.4.1+sha512.…` form; a field that names bun but does
  not parse (a range, a typo) warns, and an absent field or another package
  manager is ignored silently.

  **The workflow pin is not checked.** The runner reads `package.json`, never
  `.github/workflows/ci.yml`, so bumping one and forgetting the other ships two
  runtimes with every gate green. Change both in the same commit.

When porting to an existing repo, pin the runtime the same way — add
`packageManager` and the CI `bun-version` together, and treat them as one edit.
