# my-project

Python project template with built-in guardrails: linting, formatting, type-checking, and testing.

## Setup

```bash
uv sync                           # Install dependencies
uv run hooks                      # Install git hooks
```

## Development

```bash
uv run check                      # Fix + format + typecheck (after editing)
uv run pre-commit                 # Staged checks + tests (runs via git hook)
uv run ci                         # Lint + format check + typecheck + tests with coverage
```

All commands minimize output — only errors are shown. Add `--verbose` for full output via the CLI:

```bash
uv run python dev.py check --verbose
```

### Individual commands

```bash
uv run fix                        # Fix lint errors
uv run format                     # Format code
uv run lint                       # Lint check (read-only)
uv run typecheck                  # Type-check with basedpyright
uv run test                       # Run tests
uv run test-cov                   # Tests with coverage (80% minimum)
uv run install                    # Install dependencies
uv run clean                      # Remove caches
uv run python dev.py help         # List all commands
```

## Project Structure

```
src/          Source code
tests/        Tests
dev.py        Development task runner (zero dependencies)
scripts/      Utility scripts
```

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `pyproject.toml`
3. `uv sync && uv run hooks`
4. Start coding in `src/`
