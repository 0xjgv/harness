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
  separate acceptance step in `check` — then complexity and duplication,
  then warns (does not
  block) via `arch-config-guard` and `gherkin-guard`, checks `agents-md-drift`,
  and ratchets suppressions. Invariant: `ci` minus `check` == every gate that
  needs the network or a build lock (`audit`, `coverage`, advisory `crap`)
  plus the architecture boundary check itself (`arch`, which stays
  `ci`/`pre-push`-only because cargo-modules takes cargo's exclusive build
  lock on `target/`). `ci` runs the read-only gates (`clippy`, `format check`,
  `complexity`, `acceptance`, `arch`) **in parallel** — captured and
  printed in submission order, run to completion so one pass surfaces every
  failure — then the duplication count gate (outside that batch: lizard exits
  0 whatever it finds, so the verdict is the runner's count comparison), then
  runs `audit`, streams `tests` + `coverage`, and the
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
  lizard `--csv` with `target/llvm-cov/lcov.info`). Suppressions,
  `coverage.min`, `complexity.max_violations`, `crap.max_violations` and
  `duplication.max_blocks` are all
  ratcheted by `.harness-baseline`. `complexity` passes its floor to lizard as
  `-i N`; `crap` compares its offender count to `crap.max_violations`. The
  `complexity` command also runs a second lizard pass over the same targets
  (`-Eduplicate -w -i`), counts the `Duplicate block:` reports and compares them
  to `duplication.max_blocks` — a `✗` and exit 1 over the floor, report-only
  without one, an error (never a silent 0) when lizard itself fails. lizard only
  reports a block once it repeats for ~70 tokens, and both gates read `src` +
  `tests` only (`harness.rs` is a crate-root `[[bin]]` outside that set), so the
  shipped template measures 0 blocks — the floor matters in an adopting repo,
  where it starts wherever that repo already is. A missing
  file, or a missing key, makes either gate report-only — labelled `report-only:
  no .harness-baseline floor`, with the hint to record one — and it passes, for
  `crap --enforce` too: nothing recorded is a repo that was never measured, not
  a floor of zero, and a legacy tree has to be green on day one.
  `suppressions --update-baseline` measures all four, merges them over the
  existing file (unknown keys such as `mutation.min` are preserved untouched, a
  suppression kind that ratcheted to zero is recorded as `0`), and is
  all-or-nothing: a metric that cannot be measured aborts the write, a metric
  that does not apply has its key dropped with a warning. `coverage.min` is the
  measured total truncated, never rounded up. Requires `uvx` on PATH
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
