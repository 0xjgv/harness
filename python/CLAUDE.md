# CLAUDE

## Commands

- After edits: `uv run harness check` — fix, format, typecheck, test (or syntax check when no tests exist), suppression ratchet
- Pre-commit: `uv run harness pre-commit` — staged files only (auto via git hook): fix, format, typecheck. A staged `CLAUDE.md` is copied to `AGENTS.md` and staged with it; a hand edit to `AGENTS.md` alone fails. Arch config changes warn here, they do not fail. Tests run at pre-push.
- Pre-push: `uv run harness pre-push` — branch guard (refuses pushes to main/master unless `HARNESS_ALLOW_PROTECTED_PUSH=1`), then the test suite (alone, without git's `GIT_*` hook variables, because it writes caches), then a read-only push gate over the whole tree: lint, format check, agents-md drift, acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `uv run harness ci` — read-only gates (lint, format check, typecheck, audit, complexity, deadcode, agents-md drift, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then coverage (streams) + crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `uv run harness complexity` — uvx lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests
- Deadcode: `uv run harness deadcode` — uvx vulture@2.16 over `src/` only (`--min-confidence 60`); a dead helper that still has a test surfaces rather than hides. Allowlist dynamic references (decorator handlers, getattr) in `vulture_allowlist.py`. Runs in ci; stop-hook reports only findings on changed lines.
- Audit: `uv run harness audit` — audit dependencies for known vulnerabilities (via pip-audit)
- Acceptance: `uv run harness acceptance` — run behave against `tests/features/`
- Coverage: `uv run harness coverage --min=0` — coverage.py with threshold + uncovered listing; default comes from `.harness-baseline` `coverage.min`; warns and skips when no `tests/test*.py` files exist
- Mutation (advisory): `uv run harness mutation` — mutmut kill-rate on src/; warns and skips when no tests exist
- CRAP (advisory): `uv run harness crap --max=30` — complexity × coverage gate. Add `--enforce` to exit 1 on offenders (default exits 0 with warning). Warns and skips when no tests exist.
- Suppressions: `uv run harness suppressions` — full suppression breakdown; `--update-baseline` requires human sign-off and updates `.harness-baseline`
- Arch: `uv run harness arch` — import-linter against `.importlinter`
- Branch guard: `uv run harness branch-guard` — refuses pushes to (or deletions of) `main`/`master`; reads `HARNESS_PRE_PUSH_REFS`, else git pre-push stdin (1s deadline; partial input fails), else the current branch; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides
- Arch config guard: `uv run harness arch-config-guard` — unreviewed `.importlinter` changes warn in check/pre-commit, block pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Agents drift: `uv run harness agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `uv run harness sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `uv run harness setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and idempotently installs the Claude/Codex Stop wiring; `check` warns when the Stop or PostToolUse wiring is missing
- Stop hook: `uv run harness stop-hook` — post-edit, then lint, complexity, and dead code on the change; silent on success, exit 2 with findings on stderr (at most 20 lines). Changed lines = `git diff` against the merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then origin/HEAD, origin/main, origin/master, main, master; never fetched) plus untracked files. Lint and dead code block on changed lines. A function in `src/` or `tests/` blocks when it is over a lizard limit and the change touches its lines: leave what you touch under the limits; untouched debt never blocks. Exit 1 means a tool could not run, or the hook already blocked this stop (`stop_hook_active`). An uncommitted `CLAUDE.md` edit is copied to `AGENTS.md`. Whole-tree gates stay in check/pre-push/ci.
- Post-edit: `uv run harness post-edit` — fix + format files with uncommitted changes. `--hook` (Claude PostToolUse) does the same for the one edited file and, when it changed, prints an `additionalContext` line asking the agent to re-read it; it never blocks.

## Definition of done

- `uv run harness check` passes clean — never stop with check failing.
- Behavior worth specifying → a `.feature` scenario exists and acceptance passes; other behavior changes have unit tests.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`pre-commit` warn, and `pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- `pre-push`/`ci` must pass on your branch before you open or update a PR. Merge is the human's.
- Never spend a turn on what a tool checks: formatting, lint, types, dead code, drift, and complexity come back as `check`/`stop-hook` output. Read the output, fix the code, never the gate.

## Behavior contract

<important if="you accept a new task">
- Open with a plan: the sub-tasks, the files each touches, and which of them change user-visible behavior. Then execute the plan in the same turn.
- If the plan crosses a package or module boundary, say so in the plan so the reviewer can read the diff in that order.
</important>

<important>
## Role

- The human is the engineer. They own design, API shape, and merge authority. You propose on a branch, they merge.
- Commit and push on a feature branch as you go. Never commit to `main`/`master`, never force-push, never merge. `pre-push` refuses direct pushes to `main`/`master` unless a human sets `HARNESS_ALLOW_PROTECTED_PUSH=1`; that guard stops accidents, not `--no-verify`, so merge ownership is a rule you follow, not one the tool can enforce.
</important>

<important if="the task changes behavior">
- Specify before you build when the behavior is worth specifying: a user-visible flow, a law-like rule (formula, parser, codec, round-trip, invariant), or anything another component will depend on. Write or extend a `.feature` scenario, then step definitions, then implementation, in the same turn. The human judges in review whether the scenario earns its keep.
- Law-like behavior also gets a Hypothesis property test, not just examples — see `tests/test_properties.py` for the pattern.
- Small or incidental behavior changes may ship on unit tests alone; say so in your first response. Refactors, typo fixes, dependency bumps, and internal cleanup are not behavior changes at all.
- If it is unclear which bucket a task falls in, state your classification and proceed.
</important>

<important if="you want to edit `.importlinter` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — put the config change in its own commit whose message states the rationale, so the reviewer sees it isolated.
- The harness warns about `.importlinter` changes during `check`/`pre-commit` and blocks `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Expect the push to be refused; report it and let the human review.
</important>
