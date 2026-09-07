# Rust Template

> Rename this to your project name.

Rust project template with built-in harness: linting, formatting, testing, acceptance scenarios, coverage, a mutation advisory, and architecture checks.

## Setup

Run these commands from the root of `harness-templates`:

```bash
cp -r rust/ my-project
cd my-project
# Edit name and description in Cargo.toml before the initial commit.
git init
git add . && git commit -m "Initial Rust template"
make workspace
```

The [root workspace contract](../README.md#autonomous-workspace) lists the
supported macOS/glibc Linux platforms and the VM bootstrap layer: Make, Bash,
Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, a writable `HOME`, and `cc`
for Rust. `make workspace` ignores ambient Rust installations. It installs the
exact managed tools and locked dependencies, deploys skills, installs Git
hooks, verifies the checked-in Stop wiring, and runs `make check`. It requires
a clean tracked/index state; untracked files are preserved.

After an online run has populated the exact tool and dependency caches, the
same workspace converges without network access:

```bash
make workspace OFFLINE=1
```

Offline mode makes no network requests and fails on a cold or incomplete
cache. `make bootstrap` is a compatibility alias for `make workspace`.

### Managed toolchain

Workspace provisions exact pins: rustup 1.28.2, Rust 1.97.1, cargo-audit
0.22.2, cargo-llvm-cov 0.8.7, cargo-modules 0.26.0, and lizard 1.22.2. The
managed Rust toolchain includes `llvm-tools-preview` for coverage and CRAP.

`cargo-mutants` is intentionally absent from the managed manifest. `make
mutation` deterministically reports the advisory
`Mutation skipped (cargo-mutants is not provisioned)` result. Workspace does
not claim to provision it, and mutation remains outside `ci`.

### Dependencies and upgrades

`make deps` runs managed `cargo fetch --locked` followed by
`cargo build --locked`. In offline mode the provisioner sets
`CARGO_NET_OFFLINE=true`. These operations preserve `Cargo.lock`.

Dependency upgrades are explicit. Run Cargo through the managed boundary, then
review the lock change before committing it:

```bash
.harness/workspace.sh exec rust -- cargo update
git diff -- Cargo.lock
make deps
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
make check                 # Fix + format + lint + tests (after editing)
make pre-commit            # Staged checks + tests (runs via git hook)
make pre-push              # Read-only push gate: clippy, format check, acceptance, arch (runs via git hook)
make ci                    # Full verification (see below)
```

Make enters the checked-in managed environment for every normal harness target.
Use the full provisioner boundary when passing harness-specific arguments:

```bash
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- check --verbose
```

### `ci` pipeline

`make ci` runs the read-only gates — strict clippy (`-D warnings`), format check, complexity (lizard, CCN 15, args 8), acceptance (cucumber), arch (cargo-modules) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then runs dep audit, streams tests + coverage (cargo-llvm-cov, default threshold from `.harness-baseline`), and the advisory CRAP.

`pre-push` is the offline push gate — clippy, format check, acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

Dead code needs no separate gate — rust's `dead_code` lint is on by default and the strict clippy (`-D warnings`) denies unused functions, fields, and variants; unused dependencies surface via Cargo's own warnings.

`cmd_coverage` runs the test suite under llvm-cov once and emits both the
console summary (with the `--min=N` threshold check) and an LCOV file at
`target/llvm-cov/lcov.info`. `cmd_crap` reuses that LCOV — no second test run —
unless the file is missing or older than `src/`.

CRAP is advisory: it warns by default and exits 0 unless `--enforce` is passed.
Mutation remains advisory and outside `ci`; `make mutation` reports the managed
skip described above.

### Continuous integration

`.github/workflows/ci.yml` runs the checked-in contract on every push to `main`
and every pull request:

```bash
make workspace
make ci
```

The local and remote gates use the same managed tools and locks. The workflow
ships with the template, so copying the template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- check --verbose
```

### Quality subcommands

```bash
make acceptance                    # cucumber against tests/features/
make complexity                    # managed lizard CCN gate (≤15, args≤8) over src + tests
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- coverage --min=80
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- crap --max=30
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- crap --enforce
.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- suppressions
make mutation                      # deterministic advisory skip; see Managed toolchain
make arch                          # managed cargo-modules checks against arch.toml
```

### Individual commands

```bash
make fix                   # Fix lint errors (clippy --fix) + format
make lint                  # Lint + format check (read-only)
make test                  # Run tests
make pre-push              # Read-only push gate: clippy, format check, acceptance, arch
make clean                 # Remove build artifacts
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

`make workspace` installs the Git hooks and verifies the checked-in Claude and
Codex Stop configuration. Every Git and Stop hook enters the Git root and calls
`make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the
exact managed environment. Unknown existing Git hooks cause setup to fail
without modification. Only exact legacy harness shims are migrated.

## Architecture gate

Rust's compiler enforces visibility (`pub` / private) and crate-level layering,
but it does **not** forbid circular dependencies between modules of one crate,
nor flag orphan source files. Those are the invariants `make arch`
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
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- suppressions --update-baseline` only with human sign-off.
- Complexity is gated at CCN 15 and args 8 via lizard; lower it once the codebase is clean.
- CRAP is advisory (`.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- crap --max=30` is the starting ceiling). Use the managed `crap --enforce` form above to make it blocking once your team has paid down the existing offenders.
- Mutation deterministically reports its advisory skip because cargo-mutants is not in the managed manifest.
- `arch.toml` ships with two starter rules (no cycles, no orphans). Extend as the module graph grows.
- `tests/features/` ships one smoke scenario. An empty features directory warns and passes — add real scenarios before writing user-visible behavior.

## Starting from This Template

```bash
cp -r rust/ my-project
cd my-project
# Customize name and description in Cargo.toml before the initial commit.
git init
git add . && git commit -m "Initial Rust template"
make workspace
```

Start coding in `src/`. Add real scenarios under `tests/features/` before
writing user-visible behavior.
