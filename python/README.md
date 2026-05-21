# Python Template

> Rename this to your project name.

Python project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

```bash
uv sync                              # Install dependencies
uv run harness setup-hooks           # Install git pre-commit hook
```

## Development

See the [3-script contract](../README.md#the-3-script-contract) for the full rationale.

```bash
uv run harness check                 # Fix + format + typecheck + tests (after editing)
uv run harness pre-commit            # Staged checks + tests (runs via git hook)
uv run harness ci                    # Full verification (see below)
```

### `ci` pipeline

`harness ci` runs, in order: lint → format check → typecheck → dep audit → complexity (lizard, CCN 15) → acceptance (behave) → coverage (coverage.py, `--min=0` by default) → arch (import-linter).

Mutation testing and CRAP are **advisory**: not wired into `ci`; invoke explicitly.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
uv run harness check --verbose
```

### Quality subcommands

```bash
uv run harness acceptance            # behave against tests/features/
uv run harness coverage --min=80     # tests with coverage, fails below threshold
uv run harness mutation              # mutmut kill-rate on src/ (advisory; see note below)
uv run harness crap --max=30         # CRAP = CCN² × (1-cov)³ + CCN per function (advisory)
uv run harness crap --changed-only   # limit CRAP to files changed vs origin/main
uv run harness arch                  # import-linter against .importlinter
```

### Individual commands

```bash
uv run harness fix                   # Fix lint errors
uv run harness format                # Format code
uv run harness lint                  # Lint check (read-only)
uv run harness typecheck             # Type-check with basedpyright
uv run harness test                  # Run tests
uv run harness clean                 # Remove caches
```

## Project Structure

```bash
src/                 Source code
tests/               Tests (unit)
tests/features/      Gherkin scenarios (behave)
tests/features/steps/  Step definitions
harness.py           Development task runner (zero dependencies)
.importlinter        Architecture rules (import-linter)
.claude/scripts/     Hook scripts (session reinject, commit-intent classifier, pre-tool gates)
```

## Behavior contract

`CLAUDE.md` encodes an AI behavior contract enforced by hooks:

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: `git commit` / `git push` denied unless the user's current prompt explicitly asked (verbs: `commit`, `push`, `ship`, `land`, `merge`).
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Config write-protection**: edits to `.importlinter` denied unless the user names the path in their prompt.

Hook scripts live in `.claude/scripts/` and are wired via `.claude/settings.json`.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — raise over time.
- Mutation / CRAP are advisory — enable as blocking gates once baselines are established.
- `mutmut 3.x` isolates `src/` into a `mutants/` subdir and runs pytest from there. If your tests import top-level modules (e.g., `from harness import ...`), add `[tool.mutmut]` config or a `conftest.py` path shim so the isolated test run can resolve them.
- `.importlinter` ships with one starter rule (`tests` cannot import `src.internal`). Extend as the module graph grows.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `pyproject.toml`
3. `uv sync && uv run harness setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
