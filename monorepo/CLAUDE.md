# CLAUDE

## Commands

- After edits: `make check` — dispatches `check` to every subproject (fix, format, typecheck, test, suppression report)
- Pre-commit: `make pre-commit` — runs only in subprojects with staged files (auto via git hook)
- CI: `make ci` — read-only gate across every subproject; each runs its own `harness ci` (lint, typecheck, dep audit, complexity, acceptance, coverage, arch)
- Scope to one subproject: `make check-<subproject>` (e.g. `make check-api`, `make ci-web`)
- Scope to dirty subprojects: `make check-dirty` (working-tree + untracked changes)
- Parallel fan-out: `PARALLEL=1 make check` — opt-in, buffered per-subproject output. Keep off for CI and agent-visible runs.
- List subprojects: `make list`
- Setup: `make bootstrap` — per-language install + install the root git hook
- Auto-format: runs automatically after Claude edits via `Stop` hook (`make post-edit`)

Each subproject keeps its own zero-dep harness (`harness.ts` / `harness.py` / `harness.go` / `cargo harness`). The Makefile only dispatches — never reimplements lint, format, or test logic. Running a subproject's harness directly from its own directory still works:

```bash
cd api && uv run harness check
```

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
- Workflow: write or extend a `.feature` scenario in the affected subproject → get human approval → write step definitions → write implementation.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit a subproject's arch config">
- Each language subproject has its own arch config: `.importlinter` (python), `.dependency-cruiser.json` (bun), `.go-arch-lint.yml` (go), `arch.toml` (rust).
- Do not silently edit an arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The `PreToolUse` hook denies edits to any of these unless the user's current prompt explicitly authorized that path.
</important>
