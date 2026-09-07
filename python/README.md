# Python Template

> Rename this to your project name.

Python project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

Run these commands from the root of `harness-templates`:

```bash
cp -r python/ my-project
cd my-project
# Edit name and description in pyproject.toml before the initial commit.
git init
git add . && git commit -m "Initial Python template"
make workspace
```

The [root workspace contract](../README.md#autonomous-workspace) lists the
supported macOS/glibc Linux platforms and the small VM bootstrap layer: Make,
Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`.
`make workspace` ignores ambient Python and uv installations. It installs the
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

### Dependencies and upgrades

`make deps` restores the committed dependency graph with `uv sync --locked`;
`make deps OFFLINE=1` adds `--offline`. It never upgrades or rewrites the lock
as part of workspace convergence.

Dependency upgrades are explicit. Run native uv through the managed boundary,
then review the lock change before committing it:

```bash
.harness/workspace.sh exec python -- uv lock --upgrade
git diff -- uv.lock
make deps
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
make check                 # Fix + format + typecheck + tests/syntax check (after editing)
make pre-commit            # Staged checks + tests (runs via git hook)
make pre-push              # Read-only push gate: lint, format check, acceptance, arch (runs via git hook)
make ci                    # Full verification (see below)
```

Make enters the checked-in managed environment for every harness target. Use
the full boundary only when passing harness-specific flags:

```bash
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness check --verbose
```

### `ci` pipeline

`make ci` runs the read-only gates — lint, format check, typecheck, dep audit, complexity (lizard, CCN 15, args 8), deadcode (vulture), acceptance (behave), arch (import-linter) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then streams coverage (coverage.py, default threshold from `.harness-baseline`) and the advisory crap.

`pre-push` is the offline push gate — lint, format check, acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip). CRAP is **advisory** but still runs in `ci`. Mutation testing is advisory and invoked explicitly.

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
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness check --verbose
```

### Quality subcommands

```bash
make acceptance                       # behave against tests/features/
make deadcode                         # vulture over src/ only; allowlist in vulture_allowlist.py
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness coverage --min=80
make mutation                         # mutmut kill-rate on src/ (advisory; see note below)
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness crap --max=30
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness suppressions
make arch                             # import-linter against .importlinter
```

### Individual commands

```bash
make fix                   # Fix lint errors
make format                # Format code
make lint                  # Lint check (read-only)
make typecheck             # Type-check with basedpyright
make test                  # Run unittest tests, or py_compile when no tests/test*.py exist
make clean                 # Remove caches
```

## Project Structure

```bash
src/                 Source code
tests/               Tests (unit)
tests/features/      Gherkin scenarios (behave)
tests/features/steps/  Step definitions
harness.py           Development task runner (zero dependencies)
.importlinter        Architecture rules (import-linter)
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Agents that read either file receive the same instructions.

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: do not `git commit` / `git push` unless the user's current prompt explicitly asked.
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Arch config guard**: `.importlinter` changes warn during `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

`make workspace` installs the Git hooks and verifies the checked-in Claude and
Codex Stop configuration. Every Git and Stop hook enters the Git root and calls
`make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the
exact managed environment. Unknown existing Git hooks cause setup to fail
without modification. Only exact legacy harness shims are migrated.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `.harness/workspace.sh exec python -- uv run --frozen --no-sync harness suppressions --update-baseline` only with human sign-off.
- `make test` uses `unittest`; when no `tests/test*.py` files exist, it runs `py_compile` over `src/` and `harness.py`.
- Coverage, mutation, and CRAP warn and skip when no unit tests exist.
- CRAP is advisory in `ci`; use `.harness/workspace.sh exec python -- uv run --frozen --no-sync harness crap --enforce` when you are ready to block on it.
- Mutation is advisory — enable as a blocking gate once a baseline is established.
- `mutmut 3.x` isolates `src/` into a `mutants/` subdir. If your tests import top-level modules (e.g., `from harness import ...`), add `[tool.mutmut]` config or a `conftest.py` path shim so the isolated test run can resolve them.
- `.importlinter` ships with one starter rule (`tests` cannot import `src.internal`). Extend as the module graph grows.

## Starting from This Template

```bash
cp -r python/ my-project
cd my-project
# Customize name and description in pyproject.toml before the initial commit.
git init
git add . && git commit -m "Initial Python template"
make workspace
```

Start coding in `src/`. Add real scenarios under `tests/features/` before
writing user-visible behavior.
