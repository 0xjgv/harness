# Rust Template

> Rename this to your project name.

Rust project template with built-in harness: linting, formatting, testing, acceptance scenarios, coverage, a mutation advisory, and architecture checks.

## Setup

```bash
cargo build                          # Build the project
cargo harness setup-hooks            # Install git pre-commit + pre-push hooks; verify Claude/Codex Stop wiring
```

The acceptance, coverage, mutation, and arch gates depend on external cargo
subcommands. Each gate detects whether its tool is installed and warns + skips
when it is absent, so the template works out of the box and degrades cleanly:

```bash
cargo install cargo-llvm-cov --version 0.8.7   # coverage + CRAP (LCOV producer)
cargo install cargo-mutants  --version 27.0.0  # mutation (advisory)
cargo install cargo-modules  --version 0.26.0  # arch
cargo install cargo-audit    --version 0.22.1  # dep audit
```

These are the versions CI installs and the ones pinned as `CARGO_*_VERSION`
constants in `harness.rs`; another version only prints a `⚠` and never changes
an exit code. `rust-toolchain.toml` pins rustc the same way for rustup users.

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
cargo harness pre-commit           # Staged checks + tests (runs via git hook)
cargo harness pre-push             # Read-only push gate: clippy, format check, acceptance, arch (runs via git hook)
cargo harness ci                   # Full verification (see below)
```

### `ci` pipeline

`harness ci` runs the read-only gates — strict clippy (`-D warnings`), format check, complexity (lizard, CCN 15, args 8), acceptance (cucumber), arch (cargo-modules) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then runs dep audit, streams tests + coverage (cargo-llvm-cov, default threshold from `.harness-baseline`), and the advisory CRAP.

`pre-push` is the offline push gate — clippy, format check, acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

Dead code needs no separate gate — rust's `dead_code` lint is on by default and the strict clippy (`-D warnings`) denies unused functions, fields, and variants; unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`).

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
cargo harness coverage --min=80    # tests with coverage, fails below threshold
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
cargo harness pre-push             # Read-only push gate: clippy, format check, acceptance, arch
cargo harness setup-hooks          # Install git pre-commit + pre-push hooks; verify Claude/Codex Stop wiring (std-only)
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

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: do not `git commit` / `git push` unless the user's current prompt explicitly asked.
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Arch config guard**: `arch.toml` changes warn during `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Stop hooks are wired via `.claude/settings.json` for Claude and
`.codex/hooks.json` for Codex.

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
- CRAP is advisory (`crap --max=30` is the starting ceiling). Add `--enforce` to make it blocking once your team has paid down the existing offenders.
- Mutation is advisory — enable as a blocking gate once a baseline kill-rate is established.
- `arch.toml` ships with two starter rules (no cycles, no orphans). Extend as the module graph grows.
- `tests/features/` ships one smoke scenario. An empty features directory warns and passes — add real scenarios before writing user-visible behavior.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `Cargo.toml`
3. `cargo build && cargo harness setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
