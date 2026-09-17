# CLAUDE

## Commands

- After edits: `make check` — dispatches `check` to every subproject (fix, format, typecheck, test, suppression ratchet)
- Pre-commit: `make pre-commit` — runs only in subprojects with staged files (auto via git hook): fix, format, typecheck, no tests; a staged root `CLAUDE.md` is copied into `AGENTS.md` and staged; arch config changes warn here, they do not fail
- Pre-push: `make pre-push` — branch guard runs first (refuses pushes to, or deletions of, `main`/`master` unless `HARNESS_ALLOW_PROTECTED_PUSH=1`); on refusal it prints and exits immediately, before the arch config guard, the root-pair agents-md drift check, and dispatch. Once the branch guard passes: the arch config guard, then the root-pair agents-md drift check, then a read-only push gate across every subproject; each runs its own `harness pre-push` (tests, lint, format check, acceptance, arch, agents-md drift over the whole tree). The hook's refs are read once here and handed to every subproject as `HARNESS_PRE_PUSH_REFS`. Auto via git pre-push hook.
- CI: `make ci` — the root-pair agents-md drift check, then a read-only gate across every subproject; each runs its own `harness ci` — read-only gates (lint, typecheck, dep audit, complexity, deadcode where the language ships one, acceptance, arch, agents-md drift) in parallel, then coverage + crap
- CRAP (advisory): `make crap` — fan out the CRAP gate to every subproject (each runs its own `harness crap`). Forward flags via `ARGS`, e.g. `make crap ARGS="--enforce --max=50"`.
- Complexity: `make complexity` — fan out the complexity gate to every subproject (lizard CCN). Same `ARGS=...` forwarding.
- Scope to one subproject: `make check-<subproject>` (e.g. `make check-api`, `make ci-web`, `make pre-push-api`, `make crap-api`, `make complexity-api`)
- Scope to dirty subprojects: `make check-dirty` (working-tree + untracked changes)
- Parallel fan-out: `PARALLEL=1 make check` — opt-in, buffered per-subproject output. Keep off for CI and agent-visible runs.
- List subprojects: `make list`
- Branch guard: `make branch-guard` — refuses pushes to (or deletions of) `main`/`master`; reads `HARNESS_PRE_PUSH_REFS`, else git pre-push stdin (1s deadline; partial input fails), else the current branch; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides
- Arch config guard: `make arch-config-guard` — unreviewed `.importlinter`, `.dependency-cruiser.json`, `.go-arch-lint.yml`, or `arch.toml` changes warn in check/pre-commit and block pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Agents drift: `make agents-md-drift` — fail if any subproject's AGENTS.md differs from its CLAUDE.md (root pair included). Scope: `make agents-md-drift-<sub>`
- Sync: `make sync-agents-md` — overwrite each subproject's AGENTS.md from its CLAUDE.md. Scope: `make sync-agents-md-<sub>`
- Setup: `make bootstrap` — per-language install + install the root git hook
- Stop hook: `make -s stop-hook` runs each dirty subproject's `stop-hook` (fix + format, then lint on changed lines, complexity new or worse than the merge-base, dead code on changed lines where shipped) and answers as one hook JSON object; pre-existing debt never blocks a stop
- PostToolUse hook: `make -s post-edit-hook` fixes and formats each edited file in its subproject

## Definition of done

- `make check` passes clean — never stop with check failing.
- Behavior worth specifying → a `.feature` scenario exists and acceptance passes; other behavior changes have unit tests.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`pre-commit` warn, and `pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- `pre-push`/`ci` must pass on your branch before you open or update a PR. Merge is the human's.
- Never spend a turn on what a tool checks: formatting, lint, types, dead code, drift, and complexity come back as `check`/`stop-hook` output. Read the output, fix the code, never the gate.

Each subproject keeps its own zero-dep harness (`harness.ts` / `harness.py` / `harness.go` / `cargo harness`). The Makefile only dispatches — never reimplements lint, format, or test logic. Running a subproject's harness directly from its own directory still works:

```bash
cd api && uv run harness check
```

## Behavior contract

<important if="you accept a new task">
- Open with a plan: the sub-tasks, the files each touches, and which of them change user-visible behavior. Then execute the plan in the same turn.
- If the plan crosses a subproject boundary, say so in the plan so the reviewer can read the diff in that order.
</important>

<important>
## Role

- The human is the engineer. They own design, API shape, and merge authority. You propose on a branch, they merge.
- Commit and push on a feature branch as you go. Never commit to `main`/`master`, never force-push, never merge. `pre-push` refuses direct pushes to `main`/`master` unless a human sets `HARNESS_ALLOW_PROTECTED_PUSH=1`; that guard stops accidents, not `--no-verify`, so merge ownership is a rule you follow, not one the tool can enforce.
</important>

<important if="the task changes behavior">
- Specify before you build when the behavior is worth specifying: a user-visible flow, a law-like rule (formula, parser, codec, round-trip, invariant), or anything another component will depend on. Write or extend a `.feature` scenario in the affected subproject, then step definitions, then implementation, in the same turn. The human judges in review whether the scenario earns its keep.
- Law-like behavior also gets a property test with the subproject's PBT tool (hypothesis / fast-check / rapid / proptest), not just examples — see that subproject's own CLAUDE.md for the pattern.
- Small or incidental behavior changes may ship on unit tests alone; say so in your first response. Refactors, typo fixes, dependency bumps, and internal cleanup are not behavior changes at all.
- If it is unclear which bucket a task falls in, state your classification and proceed.
</important>

<important if="you want to edit a subproject's arch config (`.importlinter` python, `.dependency-cruiser.json` bun, `.go-arch-lint.yml` go, `arch.toml` rust)">
- Do not silently edit an arch config to silence a violation. Architectural violations imply a design decision — put the config change in its own commit whose message states the rationale, so the reviewer sees it isolated.
- The harness warns about arch config changes during `check`/`pre-commit`/`stop-hook` and blocks `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Expect the push to be refused; report it and let the human review.
</important>
