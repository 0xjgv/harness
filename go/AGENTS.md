# CLAUDE

## Commands

- After edits: `go run harness.go check` — fix, format, lint, test, suppression report
- Pre-commit: `go run harness.go pre-commit` — staged files only (auto via git hook)
- CI: `go run harness.go ci` — read-only pipeline: lint → audit → complexity → acceptance → test-cov → crap → arch. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `go run harness.go complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤7, length≤100) over the module
- Audit: `go run harness.go audit` — audit dependencies for known vulnerabilities (via govulncheck)
- Acceptance: `go run harness.go acceptance` — run godog against `features/`
- Coverage: `go run harness.go test-cov` — tests with race detector + `coverage.out`
- Mutation (advisory): `go run harness.go mutation` — gremlins kill-rate on `./suppressions`
- CRAP (advisory): `go run harness.go crap --max=30` — complexity × coverage gate. Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Arch: `go run harness.go arch` — go-arch-lint against `.go-arch-lint.yml`
- Agents drift: `go run harness.go agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `go run harness.go sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `go run harness.go setup-hooks` to install git pre-commit hook
- Auto-format: runs automatically after Claude edits via the `Stop` hook (post-edit)

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
- Workflow: write or extend a `.feature` scenario under `features/` → get human approval → write step definitions under `features/steps/` → write implementation.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `.go-arch-lint.yml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The `PreToolUse` hook denies edits to `.go-arch-lint.yml` unless the user's current prompt explicitly authorized it.
</important>
