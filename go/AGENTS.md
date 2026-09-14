# CLAUDE

## Commands

- After edits: `go run harness.go check` — fix, format, lint, test, suppression ratchet
- Pre-commit: `go run harness.go pre-commit` — staged files only (auto via git hook); arch config changes warn here, they do not fail
- Pre-push: `go run harness.go pre-push` — branch guard (refuses pushes to main/master unless `HARNESS_ALLOW_PROTECTED_PUSH=1`), then a read-only push gate over the whole tree: lint (golangci-lint covers format), agents-md drift, acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `go run harness.go ci` — read-only gates (lint, audit, complexity, agents-md drift, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then test-cov (streams) + crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `go run harness.go complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over the module
- Deadcode: no separate target — golangci-lint's `unused` linter (run by `lint`/`ci`) already flags unreachable functions, vars, and types, and `go mod tidy` prunes unused dependencies. (`x/tools/cmd/deadcode` needs a `main` package; this template is a library.)
- Audit: `go run harness.go audit` — audit dependencies for known vulnerabilities (via govulncheck)
- Acceptance: `go run harness.go acceptance` — run godog against `features/`
- Coverage: `go run harness.go coverage` (alias: `test-cov`) — tests with race detector + `coverage.out`; default threshold comes from `.harness-baseline` `coverage.min`
- Mutation (advisory): `go run harness.go mutation` — gremlins kill-rate on `./suppressions`
- CRAP (advisory): `go run harness.go crap --max=30` — complexity × coverage gate. Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Suppressions: `go run harness.go suppressions` — full suppression breakdown; `--update-baseline` requires human sign-off and updates `.harness-baseline`
- Arch: `go run harness.go arch` — go-arch-lint against `.go-arch-lint.yml`
- Branch guard: `go run harness.go branch-guard` — refuses pushes to (or deletions of) `main`/`master`; reads `HARNESS_PRE_PUSH_REFS`, else git pre-push stdin (1s deadline; partial input fails), else the current branch; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides
- Arch config guard: `go run harness.go arch-config-guard` — unreviewed `.go-arch-lint.yml` changes warn in check/pre-commit/stop-hook, block pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Agents drift: `go run harness.go agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `go run harness.go sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `go run harness.go setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and idempotently installs the Claude/Codex Stop wiring
- Stop hook: auto-formats/fixes changed files, then runs complexity (`stop-hook`)

## Definition of done

- `go run harness.go check` passes clean — never stop with check failing.
- Behavior worth specifying → a `.feature` scenario exists and acceptance passes; other behavior changes have unit tests.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`pre-commit`/`stop-hook` warn, and `pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
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
- Law-like behavior also gets a rapid property test, not just examples — see `crap/properties_test.go` for the pattern.
- Small or incidental behavior changes may ship on unit tests alone; say so in your first response. Refactors, typo fixes, dependency bumps, and internal cleanup are not behavior changes at all.
- If it is unclear which bucket a task falls in, state your classification and proceed.
</important>

<important if="you want to edit `.go-arch-lint.yml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — put the config change in its own commit whose message states the rationale, so the reviewer sees it isolated.
- The harness warns about `.go-arch-lint.yml` changes during `check`/`pre-commit`/`stop-hook` and blocks `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Expect the push to be refused; report it and let the human review.
</important>
