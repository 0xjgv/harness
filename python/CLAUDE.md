# CLAUDE

## Commands

- After edits: `uv run harness check` — fix, format, typecheck, test (or syntax check when no tests exist), suppression report
- Pre-commit: `uv run harness pre-commit` — staged files only (auto via git hook)
- Pre-push: `uv run harness pre-push` — read-only push gate over the whole tree: lint, format check, acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `uv run harness ci` — read-only gates (lint, format check, typecheck, audit, complexity, deadcode, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then coverage (streams) + crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `uv run harness complexity` — uvx lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests
- Deadcode: `uv run harness deadcode` — uvx vulture@2.16 over `src/` only (`--min-confidence 60`); a dead helper that still has a test surfaces rather than hides. Allowlist dynamic references (decorator handlers, getattr) in `vulture_allowlist.py`. Runs in ci + stop-hook.
- Audit: `uv run harness audit` — audit dependencies for known vulnerabilities (via pip-audit)
- Acceptance: `uv run harness acceptance` — run behave against `tests/features/`
- Coverage: `uv run harness coverage --min=0` — coverage.py with threshold + uncovered listing; warns and skips when no `tests/test*.py` files exist
- Mutation (advisory): `uv run harness mutation` — mutmut kill-rate on src/; warns and skips when no tests exist
- CRAP (advisory): `uv run harness crap --max=30` — complexity × coverage gate. Add `--enforce` to exit 1 on offenders (default exits 0 with warning). Warns and skips when no tests exist.
- Arch: `uv run harness arch` — import-linter against `.importlinter`
- Agents drift: `uv run harness agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `uv run harness sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `uv run harness setup-hooks` to install git pre-commit and verify Claude/Codex Stop hook wiring
- Stop hook: auto-formats/fixes changed files, then runs complexity and deadcode (parallel) and advisory CRAP (`stop-hook`)

## Behavior contract

<important if="you accept a new task">
- Restate the task as at most 5 sub-tasks. Each sub-task MUST touch ≤1 non-test file and ≤1 test.
- If the task cannot be decomposed within that bound, STOP and return a decomposition proposal. Do NOT edit code in the same turn.
- If a proposed sub-task would edit more than one non-test file, split it further before writing code.
</important>

<important>
## Role

- The human is the engineer. They own design, API shape, and merge authority. You propose, they dispose.
- Do NOT run `git commit`, `git push`, or equivalent publishing commands unless the user's current prompt asked for it. The verbs `commit`, `push`, `ship`, `land`, `merge` in action context authorize that turn only.
- If you decide on your own to "commit this and move on," the `PreToolUse` hook will deny the command. That is working as intended.
</important>

<important if="the task changes user-visible behavior">
- Workflow: write or extend a `.feature` scenario → get human approval → write step definitions → write implementation.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a Hypothesis property test, not just examples — see `tests/test_properties.py` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `.importlinter` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The `PreToolUse` hook denies edits to `.importlinter` unless the user's current prompt explicitly authorized it.
</important>
