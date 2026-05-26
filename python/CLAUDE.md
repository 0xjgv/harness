# CLAUDE

## Commands

- After edits: `uv run harness check` — fix, format, typecheck, test, suppression report
- Pre-commit: `uv run harness pre-commit` — staged files only (auto via git hook)
- CI: `uv run harness ci` — read-only pipeline: lint → format check → typecheck → audit → complexity → acceptance → coverage → crap → arch. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `uv run harness complexity` — uvx lizard@1.22.2 CC gate (CCN≤15, args≤7, length≤100) over src + tests
- Audit: `uv run harness audit` — audit dependencies for known vulnerabilities (via pip-audit)
- Acceptance: `uv run harness acceptance` — run behave against `tests/features/`
- Coverage: `uv run harness coverage --min=0` — coverage.py with threshold + uncovered listing
- Mutation (advisory): `uv run harness mutation` — mutmut kill-rate on src/
- CRAP (advisory): `uv run harness crap --max=30` — complexity × coverage gate. Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Arch: `uv run harness arch` — import-linter against `.importlinter`
- Setup: `uv run harness setup-hooks` to install git pre-commit hook
- Auto-format: runs automatically after Claude edits via `Stop` hook (post-edit)

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
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `.importlinter` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The `PreToolUse` hook denies edits to `.importlinter` unless the user's current prompt explicitly authorized it.
</important>
