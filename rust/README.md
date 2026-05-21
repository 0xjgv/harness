# Rust Template

> Rename this to your project name.

Rust project template with built-in harness: linting, formatting, testing, acceptance scenarios, coverage, a mutation advisory, and architecture checks.

## Setup

```bash
cargo build                          # Build the project
cargo harness setup-hooks            # Install git hooks
```

The acceptance, coverage, mutation, and arch gates depend on external cargo
subcommands. Each gate detects whether its tool is installed and warns + skips
when it is absent, so the template works out of the box and degrades cleanly:

```bash
cargo install cargo-llvm-cov         # coverage
cargo install cargo-mutants          # mutation (advisory)
cargo install cargo-modules          # arch
cargo install cargo-audit            # dep audit
```

`cargo-llvm-cov` needs the LLVM coverage tools. With a rustup-managed toolchain,
`rustup component add llvm-tools-preview` installs them. The harness also falls
back to a system LLVM (`brew install llvm`, or your distro's package) when the
rustup component is unavailable.

## Development

See the [3-script contract](../README.md#the-3-script-contract) for the full rationale.

```bash
cargo harness check                # Fix + format + lint + tests (after editing)
cargo harness pre-commit           # Staged checks + tests (runs via git hook)
cargo harness ci                   # Full verification (see below)
```

### `ci` pipeline

`harness ci` runs, in order: strict clippy (`-D warnings`) → format check → dep audit → tests → acceptance (cucumber) → coverage (cargo-llvm-cov, `--min=0` by default) → arch (cargo-modules).

Mutation testing is **advisory**: not wired into `ci`; invoke explicitly.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
cargo harness check --verbose
```

### Quality subcommands

```bash
cargo harness acceptance           # cucumber against tests/features/
cargo harness coverage --min=80    # tests with coverage, fails below threshold
cargo harness mutation             # cargo-mutants kill-rate (advisory)
cargo harness arch                 # cargo-modules checks against arch.toml
```

### Individual commands

```bash
cargo harness fix                  # Fix lint errors (clippy --fix) + format
cargo harness lint                 # Lint + format check (read-only)
cargo harness test                 # Run tests
cargo harness setup-hooks          # Install git pre-commit hook
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
.claude/scripts/      Hook scripts (session reinject, commit-intent classifier, pre-tool gates)
```

## Behavior contract

`CLAUDE.md` encodes an AI behavior contract enforced by hooks:

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: `git commit` / `git push` denied unless the user's current prompt explicitly asked (verbs: `commit`, `push`, `ship`, `land`, `merge`).
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Config write-protection**: edits to `arch.toml` denied unless the user names the path in their prompt.

Hook scripts live in `.claude/scripts/` and are wired via `.claude/settings.json`.

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

- `coverage --min=0` — raise over time as the suite matures.
- Mutation is advisory — enable as a blocking gate once a baseline kill-rate is established.
- `arch.toml` ships with two starter rules (no cycles, no orphans). Extend as the module graph grows.
- `tests/features/` ships one smoke scenario. An empty features directory warns and passes — add real scenarios before writing user-visible behavior.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `Cargo.toml`
3. `cargo build && cargo harness setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
