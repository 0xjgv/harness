# rust

Source: `~/Code/harness-templates/rust/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/rust/CLAUDE.md` verbatim; do not
paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `pre-push`, `ci`, `audit`, plus
  quality subcommands `complexity`, `acceptance`, `coverage`, `mutation`,
  `crap`, `arch`, `arch-config-guard`, `branch-guard`, `suppressions`, and the drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift, except that `pre-commit` first copies a staged `CLAUDE.md`
  to `AGENTS.md` and stages it; a hand edit to `AGENTS.md` alone still fails). `ci` runs the read-only gates (`clippy`, `format check`,
  `complexity`, `acceptance`, `arch`) **in parallel** — captured and
  printed in submission order, run to completion so one pass surfaces every
  failure — then runs `audit`, streams `tests` + `coverage`, and the
  advisory `crap`; `ci` also runs `arch-config-guard` in strict mode.
  `pre-commit` runs `clippy --fix` + `fmt` when Rust files are staged; it no
  longer runs tests.
  `pre-push` is the offline push gate: after `branch-guard` refuses
  `main`/`master`, it runs the test suite (alone, with git's `GIT_*` hook
  variables stripped, since the suite writes `target/` and runs `git init` in
  temp dirs), then `clippy`, `format check`, `acceptance`, `arch`, and strict
  `arch-config-guard` over the whole pushed tree (the deterministic checks
  pre-commit and stop-hook skip).
  `stop-hook` runs post-edit, then changed-lines lint and complexity delta, in
  parallel. It prints nothing on success and exits 2 with findings on stderr
  (at most 20 lines; `--verbose` lifts the cap). Changed lines come from
  `git diff -U0` against the merge-base with the base branch
  (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then `origin/HEAD`, `origin/main`,
  `origin/master`, `main`, `master`; never fetched), plus untracked files.
  Post-edit is rustfmt only, on uncommitted `.rs` files, through stdin so it
  never follows `mod` into untouched files; `cargo clippy --fix` is
  crate-wide and stays in `check`/`fix`/`pre-commit`. Lint residue runs
  `cargo clippy --message-format=json` (the `[lints]` from `Cargo.toml`,
  without `-D warnings`, which would stop at the first failing target) and
  keeps warnings and errors whose primary span sits on a changed line; a
  build that fails with nothing on a changed line is a tool failure. The
  complexity delta runs `lizard --csv` on changed `src/` and `tests/` files,
  now and at the base (`git show <base>:./<path>`), keyed by `long_name`
  with a unique-name fallback; a function blocks only when it is over a
  limit and new or worse than at the base. There is no dead-code delta:
  rustc's `dead_code` reaches the agent through lint residue. Exit 1 means a
  tool could not run, or the same payload came back with
  `stop_hook_active: true` (loop guard, state under
  `git rev-parse --git-path harness`, keyed by a 64-bit FNV-1a digest
  because std has no SHA-256). An uncommitted `CLAUDE.md` edit is copied to
  `AGENTS.md`. No arch-config warning and no whole-tree gates at stop. The
  hook JSON is read by a small std-only JSON reader in `harness.rs`.
  `post-edit --hook` (Claude PostToolUse) formats the one `.rs` file the
  event names and prints one `additionalContext` line when it changed. It
  never blocks.
  There is **no** `deadcode` target —
  rust's `dead_code` lint is on by default and `ci`'s strict clippy
  (`-D warnings`) already denies unused functions, fields, and variants;
  unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`).
  `crap` is advisory (warns by default, `--enforce` to hard-fail; joins
  lizard `--csv` with `target/llvm-cov/lcov.info`). Suppressions are ratcheted
  by `.harness-baseline`; `coverage.min` in the same file is the default coverage floor. Requires `uvx` on PATH
  for `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
  length≤100).
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

`cargo harness` is wired via `.cargo/config.toml` aliasing the `harness`
binary (`harness.rs`, declared in `Cargo.toml`); `cargo run` passes the
harness's exit code (including the stop hook's 2) through unchanged. The alias
passes `--config build.warnings='allow'`: the bin depends on the crate's lib,
so cargo would otherwise replay every cached lib warning ahead of each hook's
output. ci's strict clippy stays the gate; older cargo ignores the key and
prints those warnings (1.96 does; 1.98 honors it).
When adapting an existing repo with a different runner (e.g. `just`), rewrite
the prefix but keep the command names.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/rust/ my-project && cd my-project
cargo build && cargo harness setup-hooks
# Start coding in src/
```

This brings `AGENTS.md`/`CLAUDE.md` (Layer 2), `.claude/settings.json`,
`.codex/hooks.json`, and `.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook (`timeout` 300) and the
Claude PostToolUse hook (`matcher` `Edit|Write`, `timeout` 60);
`.codex/hooks.json` wires the Codex Stop hook only. The runner is std-only
with no JSON writer, so `setup-hooks` and `check` verify all three wirings
and warn when one is missing instead of rewriting the files; copy the
template's `.claude`/`.codex` to fix. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook`.
Claude PostToolUse command:
`cd $CLAUDE_PROJECT_DIR && cargo harness post-edit --hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh cargo harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/rust/harness.rs` (the `harness` bin in `Cargo.toml`)
- Cargo alias: `~/Code/harness-templates/rust/.cargo/`
- Tooling: rustfmt, clippy (pedantic + `unsafe_code = "forbid"`),
  `cargo test`, cargo-audit, lizard (complexity, via `uvx`),
  cargo-llvm-cov (coverage), cucumber (acceptance), cargo-mutants
  (mutation), proptest (property-based tests, see `mod property_tests`
  in `harness.rs`), cargo-modules (arch)
- Protected arch config: `arch.toml` (`cargo harness arch-config-guard`)
- Branch guard: `cargo harness branch-guard` (warns nothing, fails on `main`/`master`; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides)
