# Python Template

> Rename this to your project name.

Python project template with built-in harness: linting, formatting, type-checking, and testing.

## Setup

```bash
uv sync                                # Install dependencies
uv run harness setup-hooks # Install git pre-commit hook
```

## Development

See the [3-script contract](../README.md#the-3-script-contract) for the full rationale.

```bash
uv run harness check              # Fix + format + typecheck + tests (after editing)
uv run harness pre-commit         # Staged checks + tests (runs via git hook)
uv run harness ci                 # Lint + format check + typecheck + complexity gate + tests with coverage
```

`ci` runs a cyclomatic-complexity gate via [lizard](https://github.com/terryyin/lizard) (CCN 15), pinned as a dev dependency.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
uv run harness check --verbose
```

### Individual commands

```bash
uv run harness fix                # Fix lint errors
uv run harness format             # Format code
uv run harness lint               # Lint check (read-only)
uv run harness typecheck          # Type-check with basedpyright
uv run harness test               # Run tests
uv run harness clean              # Remove caches
```

## Project Structure

```bash
src/          Source code
tests/        Tests
harness.py    Development task runner (zero dependencies)
```

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `pyproject.toml`
3. `uv sync && uv run harness setup-hooks`
4. Start coding in `src/`
