# Bun Template

> Rename this to your project name.

Bun project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

Run these commands from the root of `harness-templates`:

```bash
cp -r bun/ my-project
cd my-project
# Edit name and description in package.json before the initial commit.
git init
git add . && git commit -m "Initial Bun template"
make workspace
```

The [root workspace contract](../README.md#autonomous-workspace) lists the
supported macOS/glibc Linux platforms and the small VM bootstrap layer: Make,
Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`.
`make workspace` ignores ambient Bun installations. It installs the exact
managed tools and locked dependencies, deploys skills, installs Git hooks,
verifies the checked-in Stop wiring, and runs `make check`.
It requires a clean tracked/index state; untracked files are preserved.

After an online run has populated the exact tool and dependency caches, the
same workspace converges without network access:

```bash
make workspace OFFLINE=1
```

Offline mode makes no network requests and fails on a cold or incomplete
cache. `make bootstrap` is a compatibility alias for `make workspace`.

### Dependencies and upgrades

`make deps` restores the committed dependency graph with
`bun install --frozen-lockfile`; `make deps OFFLINE=1` adds `--offline`. It
never upgrades or rewrites `bun.lock` as part of workspace convergence.

Dependency upgrades are explicit. Run native Bun through the managed boundary,
then review the lock change before committing it:

```bash
.harness/workspace.sh exec bun -- bun update
git diff -- bun.lock
make deps
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
make check                 # Fix + format + typecheck + tests/no-test warning (after editing)
make pre-commit            # Staged checks + tests (runs via git hook)
make pre-push              # Read-only push gate: lint, acceptance, arch (runs via git hook)
make ci                    # Full verification (see below)
```

Make enters the checked-in managed environment for every harness target. Use
the full boundary only when passing harness-specific flags:

```bash
.harness/workspace.sh exec bun -- bun harness.ts check --verbose
```

### `ci` pipeline

`make ci` runs the read-only gates — lint + format check (biome), typecheck (tsc), dep audit (bun audit), complexity (lizard, CCN 15, args 8), deadcode (knip), acceptance (cucumber), arch (dependency-cruiser) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then streams coverage (`bun test --coverage`, default threshold from `.harness-baseline`) and the advisory crap.

`pre-push` is the offline push gate — lint (biome covers format), acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

The provisioner supplies exact managed pins for lizard 1.22.2 and knip 5.88.1;
neither gate depends on ambient tools.

CRAP is **advisory** but still runs in `ci`. Mutation testing is advisory and invoked explicitly.

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
.harness/workspace.sh exec bun -- bun harness.ts check --verbose
```

### Quality subcommands

```bash
make acceptance                    # cucumber against tests/features/
make deadcode                      # managed knip; unused files/exports/deps; config in knip.json
.harness/workspace.sh exec bun -- bun harness.ts coverage --min=80
make mutation                      # Stryker mutation score on src/ (advisory)
.harness/workspace.sh exec bun -- bun harness.ts crap --max=30
.harness/workspace.sh exec bun -- bun harness.ts suppressions
make arch                          # dependency-cruiser against .dependency-cruiser.json
```

### Individual commands

```bash
make fix                   # Fix lint errors + format code
make lint                  # Lint + format check (read-only)
make typecheck             # Type-check with tsc
make test                  # Run tests
make clean                 # Remove caches
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

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: do not `git commit` / `git push` unless the user's current prompt explicitly asked.
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Arch config guard**: `.dependency-cruiser.json` changes warn during `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

`make workspace` installs the Git hooks and verifies the checked-in Claude and
Codex Stop configuration. Every Git and Stop hook enters the Git root and calls
`make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the
exact managed environment. Unknown existing Git hooks cause setup to fail
without modification. Only exact legacy harness shims are migrated.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `.harness/workspace.sh exec bun -- bun harness.ts suppressions --update-baseline` only with human sign-off.
- `make test`, coverage, mutation, and CRAP warn and skip when no test files exist.
- CRAP is advisory in `ci`; use `.harness/workspace.sh exec bun -- bun harness.ts crap --enforce` when you are ready to block on it.
- Mutation is advisory — enable as a blocking gate once a baseline is established.
- StrykerJS has no official Bun test-runner plugin; `stryker.conf.json` uses the universal `command` runner, which shells out to `bun test` and grades each mutant by exit code. It works everywhere but cannot do per-test coverage optimizations — expect a full test run per mutant.
- `.dependency-cruiser.json` ships with one starter rule (`src/internal/` is not importable from outside it) plus a `no-circular` rule. Extend as the module graph grows.

## Starting from This Template

```bash
cp -r bun/ my-project
cd my-project
# Customize name and description in package.json before the initial commit.
git init
git add . && git commit -m "Initial Bun template"
make workspace
```

Start coding in `src/`. Add real scenarios under `tests/features/` before
writing user-visible behavior.
