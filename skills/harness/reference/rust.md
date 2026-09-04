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
  `crap`, `arch`, `arch-config-guard`, `gherkin-guard`, `suppressions`, and
  the drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift). `check` runs clippy `--fix`, `cargo fmt`, and tests — the
  `acceptance` `[[test]]` target runs under plain `cargo test`, so this
  already covers the cucumber scenarios, unlike python/bun/go, which need a
  separate acceptance step in `check` — then complexity, then warns (does not
  block) via `arch-config-guard` and `gherkin-guard`, checks `agents-md-drift`,
  and ratchets suppressions. Invariant: `ci` minus `check` == every gate that
  needs the network or a build lock (`audit`, `coverage`, advisory `crap`)
  plus the architecture boundary check itself (`arch`, which stays
  `ci`/`pre-push`-only because cargo-modules takes cargo's exclusive build
  lock on `target/`). `ci` runs the read-only gates (`clippy`, `format check`,
  `complexity`, `acceptance`, `arch`) **in parallel** — captured and
  printed in submission order, run to completion so one pass surfaces every
  failure — then runs `audit`, streams `tests` + `coverage`, and the
  advisory `crap`; `ci` also runs `arch-config-guard` and `gherkin-guard` in
  strict (blocking) mode.
  `pre-push` is the offline push gate: `clippy`, `format
  check`, `acceptance`, `arch`, and strict `arch-config-guard` + `gherkin-guard` over the whole pushed tree (the deterministic
  checks pre-commit and stop-hook skip). There is **no** `deadcode` target —
  rust's `dead_code` lint is on by default and `ci`'s strict clippy
  (`-D warnings`) already denies unused functions, fields, and variants;
  unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`).
  `gherkin-guard` blocks a changed `src/` file (excluding `harness.rs`) with
  no changed `.feature` in `pre-commit`/`pre-push`/`ci`
  (`HARNESS_ALLOW_NO_FEATURE=1` overrides after review); it only warns in
  `check`/`stop-hook`, and skips silently when the repo has no `.feature`
  files anywhere.
  `crap` is advisory (warns by default, `--enforce` to hard-fail; joins
  lizard `--csv` with `target/llvm-cov/lcov.info`). `check` and `pre-commit`
  scope `test` to the changed modules — `src/foo/bar.rs` → the libtest filter
  `foo::bar`, `src/main.rs` and `src/bin/*.rs` → `--bins`, `tests/<name>.rs` →
  `--test <name>`, any `.feature` → `--test acceptance`, `harness.rs` →
  `--bins` + `--test acceptance`. Filters never run bare (that would forward
  them to the `harness = false` acceptance target): they run under `--lib`, or
  `--bins` in a crate with no lib target. The change set is the staged files in
  `pre-commit`, else `<base>...HEAD` when `--base=<ref>` / `HARNESS_ARCH_BASE` /
  `GITHUB_BASE_REF` resolves, else the uncommitted files. An empty scope warns
  and skips (it never widens); a changed source with no `#[cfg(test)]` block
  warns once and never fails; a `--base=<ref>` git cannot resolve fails the
  gate instead of degrading to a scope that would test nothing. `--all` and `ci` run the whole suite. Suppressions are ratcheted
  by `.harness-baseline`; `coverage.min` in the same file is the default coverage floor. Requires `uvx` on PATH
  for `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
  length≤100).
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

`cargo harness` is wired via `.cargo/config.toml` aliasing the binary in
`src/main.rs`. When adapting an existing repo with a different runner
(e.g. `just`), rewrite the prefix but keep the command names.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/rust/ my-project && cd my-project
cargo build && cargo harness setup-hooks
# Start coding in src/
```

This brings `AGENTS.md`/`CLAUDE.md` (Layer 2), `.claude/settings.json`,
`.codex/hooks.json`, and `.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook; `.codex/hooks.json` wires
the Codex Stop hook. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && cargo harness stop-hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh cargo harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/rust/harness.rs` (entry: `src/main.rs`)
- Cargo alias: `~/Code/harness-templates/rust/.cargo/`
- Tooling: rustfmt, clippy (pedantic + `unsafe_code = "forbid"`),
  `cargo test`, cargo-audit, lizard (complexity, via `uvx`),
  cargo-llvm-cov (coverage), cucumber (acceptance), cargo-mutants
  (mutation), proptest (property-based tests, see `mod property_tests`
  in `harness.rs`), cargo-modules (arch)
- Protected arch config: `arch.toml` (`cargo harness arch-config-guard`)
