# Bun Template

> Rename this to your project name.

Bun project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

```bash
bun install                         # Install dependencies
bun run setup-hooks                 # Install git pre-commit + pre-push hooks and the Claude/Codex Stop wiring
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
bun run check                      # Fix + format + typecheck + tests/no-test warning (after editing)
bun run pre-commit                 # Staged checks + tests (runs via git hook)
bun harness.ts pre-push            # Read-only push gate: branch guard, lint, acceptance, arch (runs via git hook)
bun run ci                         # Full verification (see below)
```

Every command above is also a `make` target — `make check`, `make ci`, `make pre-push`, and so on forward to the harness. `make bootstrap` does first-time setup (`bun install` + `setup-hooks`) in one step.

### `ci` pipeline

`harness ci` runs the read-only gates — lint + format check (biome), typecheck (tsc), dep audit (bun audit), agents-md drift, complexity (lizard, CCN 15, args 8), deadcode (knip), acceptance (cucumber), arch (dependency-cruiser) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then streams coverage (`bun test --coverage`, default threshold from `.harness-baseline`) and the advisory crap.

`pre-push` is the offline push gate — a branch guard that refuses pushes to (or deletions of) `main`/`master` (unless `HARNESS_ALLOW_PROTECTED_PUSH=1`), reading `HARNESS_PRE_PUSH_REFS` or git's pre-push stdin (1s deadline; partial input fails) and falling back to the current branch, then lint (biome covers format), agents-md drift, acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

The complexity gate requires `uvx` on PATH — install via [uv](https://docs.astral.sh/uv/).

The gates split into four groups: hard quality gates that block (lint, typecheck, arch, complexity, suppressions, deadcode, audit, agents-md drift), advisory metrics that inform but never fail the build (CRAP, mutation), a ratchet that only moves up (the coverage floor in `.harness-baseline`), and two permission gates that need a human to unblock (arch-config-guard, branch-guard). CRAP is **advisory** but still runs in `ci`. Mutation testing is advisory and invoked explicitly.

### Continuous integration

`.github/workflows/ci.yml` runs `bun harness.ts ci` on every push to `main` and every pull request — the same gate you run locally, so local gate == remote gate. It installs `uv` alongside Bun so the lizard-based gates work. Copying the template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
bun harness.ts check --verbose
```

### Quality subcommands

```bash
bun run acceptance                 # cucumber against tests/features/
bun harness.ts deadcode            # knip (via bunx): unused files/exports/deps; config in knip.json
bun run coverage                   # tests with coverage, floor from .harness-baseline (--min=N for a local override)
bun run mutation                   # Stryker mutation score on src/ (advisory)
bun run crap --max=30              # CRAP = CCN² × (1-cov)³ + CCN per function (advisory)
bun harness.ts suppressions        # suppression breakdown; --update-baseline with human sign-off
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
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Agents that read either file receive the same instructions.

- **Plan first**: open with the sub-tasks and the files each touches, then execute in the same turn.
- **Human-is-engineer**: commit and push on a feature branch; `main`/`master` and merges stay human — `pre-push` refuses direct pushes to (and deletions of) them unless `HARNESS_ALLOW_PROTECTED_PUSH=1`. The guard stops accidents, not `--no-verify`.
- **Specify what is worth specifying**: `.feature` scenarios for user-visible flows, law-like rules, and cross-component contracts; unit tests suffice for the rest.
- **Arch config guard**: `.dependency-cruiser.json` changes warn during `check`/`pre-commit`/`stop-hook` and fail `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Stop hooks are wired via `.claude/settings.json` for Claude and
`.codex/hooks.json` for Codex.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `harness suppressions --update-baseline` only with human sign-off.
- `harness test`, coverage, mutation, and CRAP warn and skip when no test files exist.
- CRAP and mutation are advisory by design — they tell you where the next test or split pays off, they are not gates, because a coverage-shaped target gets satisfied with assertion-free tests. `--enforce` exists for teams that want it on CRAP; it is not the recommended default.
- The coverage floor is a ratchet: `.harness-baseline` `coverage.min` only ever moves up, by a human, and starts at 0.
- StrykerJS has no official Bun test-runner plugin; `stryker.conf.json` uses the universal `command` runner, which shells out to `bun test` and grades each mutant by exit code. It works everywhere but cannot do per-test coverage optimizations — expect a full test run per mutant.
- `.dependency-cruiser.json` ships with one starter rule (`src/internal/` is not importable from outside it) plus a `no-circular` rule. Extend as the module graph grows.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `package.json`
3. `bun install && bun run setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
