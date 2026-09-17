# Rust Template

> Rename this to your project name.

Rust project template with built-in harness: linting, formatting, testing, acceptance scenarios, coverage, a mutation advisory, and architecture checks.

## Setup

```bash
cargo build                          # Build the project
cargo harness setup-hooks            # Install git pre-commit + pre-push hooks; verify Claude/Codex agent hook wiring
```

The acceptance, coverage, mutation, and arch gates depend on external cargo
subcommands. Each gate detects whether its tool is installed and warns + skips
when it is absent, so the template works out of the box and degrades cleanly:

```bash
cargo install cargo-llvm-cov         # coverage + CRAP (LCOV producer)
cargo install cargo-mutants          # mutation (advisory)
cargo install cargo-modules          # arch
cargo install cargo-audit            # dep audit
```

The complexity and CRAP gates additionally require `uvx` on `PATH` — they shell
out to `uvx lizard@1.22.2` for the cyclomatic-complexity scan. Install via
[uv](https://docs.astral.sh/uv/).

`cargo-llvm-cov` needs the LLVM coverage tools. With a rustup-managed toolchain,
`rustup component add llvm-tools-preview` installs them. The harness also falls
back to a system LLVM (`brew install llvm`, or your distro's package) when the
rustup component is unavailable.

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
cargo harness check                # Fix + format + lint + tests (after editing)
cargo harness pre-commit           # Fix + format when Rust files are staged; mirrors a staged CLAUDE.md (runs via git hook; arch config warns)
cargo harness pre-push             # Branch guard + tests + read-only push gate: clippy, format check, acceptance, arch (runs via git hook)
cargo harness stop-hook            # post-edit, then changed-lines lint, touched-function complexity; silent on success, exit 2 with findings
cargo harness ci                   # Full verification (see below)
```

### `ci` pipeline

`harness ci` runs the read-only gates — strict clippy (`-D warnings`), format check, complexity (lizard, CCN 15, args 8), acceptance (cucumber), arch (cargo-modules) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then runs agents-md-drift as a separate hard check, dep audit, streams tests + coverage (cargo-llvm-cov, default threshold from `.harness-baseline`), and the advisory CRAP.

`pre-push` is the offline push gate — a branch guard that refuses direct pushes to (or deletions of) `main`/`master` (override: `HARNESS_ALLOW_PROTECTED_PUSH=1`) and short-circuits on refusal, printing only that and exiting before anything else runs; otherwise the arch config guard, then agents-md-drift as a separate hard check, then the test suite (alone, without git's `GIT_*` hook variables: it writes `target/`, and a test that runs `git init` would otherwise write into this repository), then clippy, format check, acceptance, arch run in parallel over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip). `pre-commit` no longer runs the tests.

### Agent hooks

The Claude/Codex Stop hook runs `stop-hook` after every agent turn and judges the change, not the tree. It formats changed `.rs` files with rustfmt, copies an uncommitted `CLAUDE.md` edit to `AGENTS.md`, then runs two read-only gates in parallel over the lines changed since the merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then origin/HEAD, origin/main, origin/master, main, master; never fetched), plus untracked files:

- **Lint** — `cargo clippy`; a warning or error on a changed `.rs` line blocks (rustc's `dead_code` arrives this way). Both the long and the short diagnostic forms are read, because cargo replays cached diagnostics in whichever form first rendered them.
- **Complexity** — lizard over changed files in `src/` + `tests/`; a function blocks when it is over a limit and overlaps a changed line. Touching an over-limit function means leaving it under the limit; an untouched one never blocks.

Clean: no output, exit 0. Findings: exit 2 with `stop-hook failed: <gates>` and at most 20 `path:line: message` lines on stderr (`--verbose` lifts the cap). A tool that cannot run (a build that fails away from the changed lines included): exit 1, which the Claude and Codex wiring treat as non-blocking. When the hook event says `stop_hook_active: true` (the agent is already continuing from a block), findings exit 1: the hook blocks once per stop, never in a loop.

The Claude PostToolUse hook runs `post-edit --hook` on the file an Edit/Write touched: rustfmt only (never `cargo clippy --fix`, which rewrites the whole crate), and when the file changed it prints an `additionalContext` line asking the agent to re-read it. It never blocks.

Dead code needs no separate gate — rust's `dead_code` lint is on by default and the strict clippy (`-D warnings`) denies unused functions, fields, and variants; unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`).

The gates split into four kinds: hard quality gates that block (clippy, format check, complexity, arch, suppressions, dead code, dep audit, agents-md-drift), advisory metrics that inform but never block (CRAP, mutation), a ratchet that only ever moves up (the coverage floor in `.harness-baseline`), and two permission gates that need a human to unblock (arch-config-guard, branch-guard).

`cmd_coverage` runs the test suite under llvm-cov once and emits both the
console summary (with the `--min=N` threshold check) and an LCOV file at
`target/llvm-cov/lcov.info`. `cmd_crap` reuses that LCOV — no second test run —
unless the file is missing or older than `src/`.

CRAP is advisory: it warns by default and exits 0 unless `--enforce` is passed.
Mutation testing is also advisory and NOT wired into `ci`; invoke explicitly.

### Continuous integration

`.github/workflows/ci.yml` runs `cargo harness ci` on every push to `main` and
every pull request — the same gate you run locally, so local gate == remote gate.
It installs `uv` (for the lizard gates) and cargo-audit / cargo-llvm-cov /
cargo-modules. Copying the template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
cargo harness check --verbose
```

Every command is also a `make` target — `make check`, `make ci`, `make pre-push`, and so on forward to the harness. `make bootstrap` does first-time setup (`cargo build` + `setup-hooks`) in one step.

### Quality subcommands

```bash
cargo harness acceptance           # cucumber against tests/features/
cargo harness complexity           # lizard CCN gate (≤15, args≤8) over src + tests
cargo harness coverage             # tests with coverage, floor from .harness-baseline (--min=N overrides locally)
cargo harness crap --max=30        # CRAP complexity × coverage gate (advisory)
cargo harness crap --enforce       # …same, but hard-fail when offenders exist
cargo harness suppressions         # suppression breakdown; --update-baseline with human sign-off
cargo harness mutation             # cargo-mutants kill-rate (advisory)
cargo harness arch                 # cargo-modules checks against arch.toml
```

### Individual commands

```bash
cargo harness fix                  # Fix lint errors (clippy --fix) + format
cargo harness lint                 # Lint + format check (read-only)
cargo harness test                 # Run tests
cargo harness pre-push             # Branch guard + read-only push gate: clippy, format check, acceptance, arch
cargo harness post-edit            # rustfmt on uncommitted .rs files (--hook: the file a PostToolUse event names)
cargo harness setup-hooks          # Install git pre-commit + pre-push hooks; verify Claude/Codex agent hook wiring (std-only)
cargo harness clean                # Remove build artifacts
```

## Project Structure

```
src/                  Source code (lib.rs + main.rs)
tests/                Integration tests
tests/acceptance.rs   Cucumber runner + step definitions (harness = false)
tests/features/       Gherkin scenarios (.feature files)
harness.rs            Development task runner (zero dependencies)
arch.toml             Architecture rules (cargo-modules)
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Agents that read either file receive the same instructions.

- **Plan then execute**: open with the sub-tasks and the files each touches, then do the work in the same turn.
- **Human-is-engineer**: commit and push on a feature branch; the human merges. The `pre-push` branch guard refuses direct pushes to (or deletions of) `main`/`master` unless `HARNESS_ALLOW_PROTECTED_PUSH=1` — it stops accidents, not `--no-verify`. It reads `HARNESS_PRE_PUSH_REFS`, else the hook's stdin refs, else the current branch.
- **Specify what is worth specifying**: `.feature` scenarios for user-visible flows, law-like rules, and cross-component contracts; unit tests suffice for the rest.
- **Arch config guard**: `arch.toml` changes warn during `check`/`pre-commit` and fail `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Stop hooks are wired via `.claude/settings.json` for Claude and
`.codex/hooks.json` for Codex; the Claude PostToolUse hook lives in
`.claude/settings.json` too.

## Architecture gate

Rust's compiler enforces visibility (`pub` / private) and crate-level layering,
but it does **not** forbid circular dependencies between modules of one crate,
nor flag orphan source files. Those are the invariants `cargo harness arch`
checks, via `cargo-modules`:

- **No module cycles** — `cargo modules dependencies --acyclic`.
- **No orphan files** — `.rs` files on disk not reachable through `mod`.

`arch.toml` declares the intent and is a write-protected path. This is the
honest Rust equivalent of Python's import-linter: it enforces a real,
compiler-unchecked invariant rather than force-fitting a layering DSL onto
Rust's module system.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `harness suppressions --update-baseline` only with human sign-off.
- Complexity is gated at CCN 15 and args 8 via lizard; lower it once the codebase is clean.
- CRAP and mutation are advisory by design — they tell you where the next test or split pays off, they are not gates, because a coverage-shaped target gets satisfied with assertion-free tests. `--enforce` on `crap` exists for teams that want it; it is not the recommended default.
- `arch.toml` ships with two starter rules (no cycles, no orphans). Extend as the module graph grows.
- `tests/features/` ships one smoke scenario. An empty features directory warns and passes — add real scenarios before writing user-visible behavior.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `Cargo.toml`
3. `cargo build && cargo harness setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
