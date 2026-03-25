# Bun Template

> Rename this to your project name.

Bun project template with built-in harness: linting, formatting, type-checking, and testing.

## Setup

```bash
bun install                         # Install dependencies
bun run hooks                       # Install git hooks
```

## Development

See the [3-script contract](../README.md#the-3-script-contract) for the full rationale.

```bash
bun run check                      # Fix + format + typecheck + tests (after editing)
bun run pre-commit                 # Staged checks + tests (runs via git hook)
bun run ci                         # Lint + typecheck + tests with coverage (CI verification)
```

All commands minimize output — only errors are shown. Add verbose mode:

```bash
VERBOSE=1 bun harness.ts
```

### Individual commands

```bash
bun harness.ts --fix                # Fix lint errors + format code
bun harness.ts --lint               # Lint + format check (read-only)
bun harness.ts --typecheck          # Type-check
bun harness.ts --test               # Run tests
bun harness.ts --clean              # Remove caches
bun harness.ts --help               # Show all flags
```

## Project Structure

```
src/          Source code
tests/        Tests
harness.ts    Pre-flight checks + development tasks (zero dependencies)
```

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `package.json`
3. `bun install && bun run hooks`
4. Start coding in `src/`
