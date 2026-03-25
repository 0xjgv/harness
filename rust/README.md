# Rust Template

> Rename this to your project name.

Rust project template with built-in harness: linting, formatting, and testing.

## Setup

```bash
cargo build                         # Build the project
cargo harness hooks                 # Install git hooks
```

## Development

See the [3-script contract](../README.md#the-3-script-contract) for the full rationale.

```bash
cargo harness check                # Fix + format + lint + tests (after editing)
cargo harness pre-commit           # Staged checks + tests (runs via git hook)
cargo harness ci                   # Read-only lint, format check, tests
```

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
cargo harness check --verbose
```

### Individual commands

```bash
cargo harness fix                  # Fix lint errors (clippy --fix) + format
cargo harness lint                 # Lint + format check (read-only)
cargo harness test                 # Run tests
cargo harness test-cov             # Run tests (cargo-llvm-cov for coverage)
cargo harness hooks                # Install git pre-commit hook
cargo harness clean                # Remove build artifacts
```

## Project Structure

```
src/          Source code (lib.rs + main.rs)
tests/        Integration tests
harness.rs    Development task runner (zero dependencies)
```

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `Cargo.toml`
3. `cargo build && cargo harness hooks`
4. Start coding in `src/`
