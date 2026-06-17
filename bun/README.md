# Bun Template

> Rename this to your project name.

Bun project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

```bash
bun install                         # Install dependencies
bun run setup-hooks                 # Install git pre-commit hook
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
bun run check                      # Fix + format + typecheck + tests/no-test warning (after editing)
bun run pre-commit                 # Staged checks + tests (runs via git hook)
bun run ci                         # Full verification (see below)
```

### `ci` pipeline

`harness ci` runs, in order: lint + format check (biome) → typecheck (tsc) → dep audit (bun audit) → complexity (lizard, CCN 15, args 8) → acceptance (cucumber) → coverage (`bun test --coverage`, `--min=0` by default) → crap (advisory) → arch (dependency-cruiser).

The complexity gate requires `uvx` on PATH — install via [uv](https://docs.astral.sh/uv/).

CRAP is **advisory** but still runs in `ci`. Mutation testing is advisory and invoked explicitly.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
bun harness.ts check --verbose
```

### Quality subcommands

```bash
bun run acceptance                 # cucumber against tests/features/
bun run coverage --min=80          # tests with coverage, fails below threshold
bun run mutation                   # Stryker mutation score on src/ (advisory)
bun run crap --max=30              # CRAP = CCN² × (1-cov)³ + CCN per function (advisory)
bun run arch                       # dependency-cruiser against .dependency-cruiser.json
```

### Individual commands

```bash
bun harness.ts fix                  # Fix lint errors + format code
bun harness.ts lint                 # Lint + format check (read-only)
bun harness.ts typecheck            # Type-check with tsc
bun harness.ts test                 # Run tests
bun harness.ts clean                # Remove caches
```

## Project Structure

```bash
src/                       Source code
tests/                     Tests (unit, bun:test)
tests/features/            Gherkin scenarios (cucumber)
tests/features/steps/      Step definitions
harness.ts                 Development task runner (zero dependencies)
.dependency-cruiser.json   Architecture rules (dependency-cruiser)
stryker.conf.json          Mutation testing config (Stryker)
cucumber.json              Acceptance runner config (cucumber)
.claude/scripts/           Hook scripts (session reinject, commit-intent classifier, pre-tool gates)
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Claude Code hooks enforce it for Claude; agents that read `AGENTS.md` receive the same instructions.

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: `git commit` / `git push` denied unless the user's current prompt explicitly asked (verbs: `commit`, `push`, `ship`, `land`, `merge`).
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Config write-protection**: edits to `.dependency-cruiser.json` denied unless the user names the path in their prompt.

Hook scripts live in `.claude/scripts/` and are wired via `.claude/settings.json`.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — raise over time.
- `harness test`, coverage, mutation, and CRAP warn and skip when no test files exist.
- CRAP is advisory in `ci`; pass `--enforce` when you are ready to block on it.
- Mutation is advisory — enable as a blocking gate once a baseline is established.
- StrykerJS has no official Bun test-runner plugin; `stryker.conf.json` uses the universal `command` runner, which shells out to `bun test` and grades each mutant by exit code. It works everywhere but cannot do per-test coverage optimizations — expect a full test run per mutant.
- `.dependency-cruiser.json` ships with one starter rule (`src/internal/` is not importable from outside it) plus a `no-circular` rule. Extend as the module graph grows.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `package.json`
3. `bun install && bun run setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
