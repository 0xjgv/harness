# Python Template

> Rename this to your project name.

Python project template with built-in harness: linting, formatting, type-checking, testing, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Setup

```bash
uv sync                              # Install dependencies
uv run harness setup-hooks           # Install git pre-commit + pre-push hooks, the Claude/Codex Stop wiring, and the Claude PostToolUse wiring
```

## Development

See the [5-script contract](../README.md#the-5-script-contract) for the full rationale.

```bash
uv run harness check                 # Fix + format + typecheck + tests/syntax check (after editing)
uv run harness pre-commit            # Staged fix/format + typecheck; mirrors a staged CLAUDE.md into AGENTS.md (runs via git hook)
uv run harness pre-push              # Branch guard + tests + read-only push gate: lint, format check, agents-md drift, acceptance, arch (runs via git hook)
uv run harness stop-hook             # post-edit, then changed-lines lint, complexity delta, deadcode delta; silent on success, exit 2 with findings (agent Stop hook)
uv run harness post-edit --hook      # Fix + format the file a Claude PostToolUse event names; never blocks
uv run harness ci                    # Full verification (see below)
```

Every command above is also a `make` target — `make check`, `make ci`, `make pre-push`, and so on forward to the harness. `make bootstrap` does first-time setup (`uv sync` + `setup-hooks`) in one step.

### `ci` pipeline

`harness ci` runs the read-only gates — lint, format check, typecheck, dep audit, complexity (lizard, CCN 15, args 8), deadcode (vulture), agents-md drift, acceptance (behave), arch (import-linter) — **in parallel**: each is captured and printed in submission order, and the batch runs to completion so one pass surfaces every failure. It then streams coverage (coverage.py, default threshold from `.harness-baseline`) and the advisory crap.

`pre-push` is the offline push gate — a branch guard (pushes to, and deletions of, `main`/`master` are refused unless `HARNESS_ALLOW_PROTECTED_PUSH=1`; destinations come from `HARNESS_PRE_PUSH_REFS`, else the hook's stdin under a 1s deadline, else the current branch), then the test suite (run alone and without git's `GIT_*` hook variables, since it writes caches), then lint, format check, agents-md drift, acceptance, arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip). `pre-commit` no longer runs tests. CRAP is **advisory** but still runs in `ci`. Mutation testing is advisory and invoked explicitly.

Gate groups: hard quality gates block the build (lint, types, arch, complexity, suppressions, dead code, audit, drift); advisory metrics report and never fail (CRAP, mutation); the coverage floor is a ratchet that only moves up; and two permission gates need a human to unblock (arch-config-guard, branch-guard).

### Continuous integration

`.github/workflows/ci.yml` runs `uv run harness ci` on every push to `main` and every pull request — the same gate you run locally, so local gate == remote gate. It ships with the template, so copying the template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
uv run harness check --verbose
```

### Quality subcommands

```bash
uv run harness acceptance            # behave against tests/features/
uv run harness deadcode              # vulture over src/ only (--min-confidence 60); allowlist in vulture_allowlist.py
uv run harness coverage              # tests with coverage, floor from .harness-baseline (--min=N overrides locally)
uv run harness mutation              # mutmut kill-rate on src/ (advisory; see note below)
uv run harness crap --max=30         # CRAP = CCN² × (1-cov)³ + CCN per function (advisory)
uv run harness suppressions          # suppression breakdown; --update-baseline with human sign-off
uv run harness arch                  # import-linter against .importlinter
uv run harness branch-guard          # refuse pushes to/deletions of main/master (HARNESS_ALLOW_PROTECTED_PUSH=1 overrides)
```

### Individual commands

```bash
uv run harness fix                   # Fix lint errors
uv run harness format                # Format code
uv run harness lint                  # Lint check (read-only)
uv run harness typecheck             # Type-check with basedpyright
uv run harness test                  # Run unittest tests, or py_compile when no tests/test*.py exist
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
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Agents that read either file receive the same instructions.

- **Plan first**: open with the sub-tasks and the files each touches, then execute in the same turn.
- **Human-is-engineer**: commit and push on a feature branch; merge is the human's. `pre-push` refuses direct pushes to `main`/`master` unless `HARNESS_ALLOW_PROTECTED_PUSH=1` — it stops accidents, not `--no-verify`.
- **Specify what is worth specifying**: `.feature` scenarios for user-visible flows, law-like rules, and cross-component contracts; unit tests suffice for the rest.
- **Arch config guard**: `.importlinter` changes warn during `check`/`pre-commit` and fail `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Stop hooks are wired via `.claude/settings.json` for Claude and
`.codex/hooks.json` for Codex; Claude also gets a PostToolUse hook
(`post-edit --hook`, Edit|Write) that formats the edited file.

`stop-hook` judges the change, not the tree. Its scope is `git diff` against the
merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then
origin/HEAD, origin/main, origin/master, main, master; never fetched) plus untracked
files. It blocks (exit 2, findings on stderr, at most 20 lines) on lint left on changed
lines, on a function in `src/` or `tests/` that is over a lizard limit and new or worse
than at the base, and on vulture findings on changed lines. Pre-existing debt never
blocks. It prints nothing on success. Exit 1 means a tool could not run, or the same
findings came back while the agent was already continuing from a stop (the loop guard,
state under `git rev-parse --git-path harness`). An uncommitted `CLAUDE.md` edit is
copied to `AGENTS.md`.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `harness suppressions --update-baseline` only with human sign-off.
- `harness test` uses `unittest`; when no `tests/test*.py` files exist, it runs `py_compile` over `src/` and `harness.py`.
- Coverage, mutation, and CRAP warn and skip when no unit tests exist.
- CRAP and mutation are advisory by design — they tell you where the next test or split pays off, they are not gates, because a coverage-shaped target gets satisfied with assertion-free tests. `--enforce` exists for teams that want it, it is not the recommended default.
- The coverage floor is a ratchet: `.harness-baseline` `coverage.min` only ever moves up, by a human, and starts at 0.
- `mutmut 3.x` isolates `src/` into a `mutants/` subdir. If your tests import top-level modules (e.g., `from harness import ...`), add `[tool.mutmut]` config or a `conftest.py` path shim so the isolated test run can resolve them.
- `.importlinter` ships with one starter rule (`tests` cannot import `src.internal`). Extend as the module graph grows.

## Starting from This Template

1. Copy this directory
2. Update `name` and `description` in `pyproject.toml`
3. `uv sync && uv run harness setup-hooks`
4. Start coding in `src/`
5. Add real scenarios under `tests/features/` before writing user-visible behavior
