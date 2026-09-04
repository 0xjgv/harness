# CLAUDE

## Commands

- After edits: `go run harness.go check` — fix, format, lint (via `--fix`), test, complexity, `go mod tidy -diff`, acceptance (self-skips with a warning when no `.feature` files exist), arch config guard (warn), Gherkin-first guard (warn), agents-md drift, suppression ratchet. **Invariant: `check` runs every gate that is offline, fast, and takes no build lock.** `ci` adds: the dependency audit (network, govulncheck), coverage, CRAP (advisory), mutation (advisory) — and, uniquely in this template, the `arch` gate: `go run github.com/fe3dback/go-arch-lint@…` fetches a module, which hits the network on a cold module cache, so it stays `ci`/`pre-push`-only rather than running in the edit-triggered `check` loop.
- Pre-commit: `go run harness.go pre-commit` — staged files only (auto via git hook). Fix/format rewrite the working tree, then the fixed files are re-staged (`git add`) so the commit records the fixed content. Caveat: if a file is only partially staged, `git add` also stages its unstaged hunks.
- Pre-push: `go run harness.go pre-push` — read-only push gate over the whole tree: lint (golangci-lint covers format), acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `go run harness.go ci` — read-only gates (lint, audit, complexity, `go mod tidy -diff`, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then test-cov (streams) + crap + mutation, then arch config guard and Gherkin-first guard, both blocking. CRAP and mutation are advisory (warn only — pass `--enforce` to hard-fail); mutation scopes itself to the base-ref diff there, never to the uncommitted set. Requires `uvx` on PATH.
- Complexity: `go run harness.go complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over the module, tolerating up to `complexity.max_violations` warnings from `.harness-baseline` (passed to lizard as `-i N`). With no such key — or no `.harness-baseline` at all — the gate is report-only: it prints `report-only: no .harness-baseline floor` and passes, so a repo adopting the harness is green on day one. Record a floor with `suppressions --update-baseline`.
- Deadcode: no separate target — golangci-lint's `unused` linter (run by `lint`/`ci`) already flags unreachable functions, vars, and types, and `go mod tidy` prunes unused dependencies. (`x/tools/cmd/deadcode` needs a `main` package; this template is a library.)
- Audit: `go run harness.go audit` — audit dependencies for known vulnerabilities (via govulncheck)
- Acceptance: `go run harness.go acceptance` — run godog against `features/`
- Coverage: `go run harness.go coverage` (alias: `test-cov`) — tests with race detector + `coverage.out`; default threshold comes from `.harness-baseline` `coverage.min`, which `suppressions --update-baseline` measures and records (total coverage truncated to an integer, never rounded up — a floor above the measured number fails the very next run). `--min=` overrides it; a non-integer value prints an error and exits 1.
- Mutation (advisory): `go run harness.go mutation` — gremlins kill-rate, scoped to the package directories of the changed `.go` files (the branch diff against a resolved base ref, unioned with the uncommitted set for local runs; `--base=<ref>` picks the base, `--all` or an explicit path argument mutates the concrete targets instead). `ci` uses the base-ref diff alone. Packages with no `*_test.go` are skipped with a `⚠`, and an empty scope skips the gate — a scoped gate never widens to the whole tree. The score is `killed / (killed + survived)` rounded, compared against `mutation.min` in `.harness-baseline`; with no such key the gate is report-only and passes even under `--enforce`. `--enforce` exits 1 below a recorded floor (default warns and exits 0). `--report=<path>` scores a report gremlins already wrote instead of spending minutes on a fresh run; a path that is unreadable, or JSON that is not a gremlins report, exits 1. A path argument that is not a package directory exits 1, and a target gremlins failed on warns — under `--enforce` it exits 1, because a score summed over only the targets that survived is not the score the floor was measured against. Scoping is package-granular because gremlins' own `--diff` silently matches nothing when it is pointed at a concrete package. Caveat worth knowing: `mutation.min` is measured over the concrete targets, so a scoped run on a weaker-than-average package can warn — which is why the gate is advisory.
- CRAP (advisory): `go run harness.go crap --max=30` — complexity × coverage gate, compared against `crap.max_violations` in `.harness-baseline`; offenders at or below the floor pass. With no such key — or no `.harness-baseline` at all — the gate is report-only: it lists the offenders, prints `report-only: no .harness-baseline floor`, and passes even under `--enforce`, so a repo adopting the harness is green on day one. Add `--enforce` to exit 1 on offenders above a recorded floor (default exits 0 with warning). A non-numeric `--max=` prints an error and exits 1.
- Suppressions: `go run harness.go suppressions` — full suppression breakdown; `--update-baseline` requires human sign-off and rewrites `.harness-baseline`. It merges: every key it measures (`suppressions.*`, `coverage.min`, `complexity.max_violations`, `crap.max_violations`) is overwritten — a kind that vanished is recorded as `0` so the floor ratchets down — and every key it does not own (anything hand-written) is carried through untouched. `mutation.min` is carried through too unless `--with-mutation` is passed, which adds it to the measured set — a mutation run costs minutes, so every baseline refresh should not pay for it. It is all-or-nothing: a metric that does not apply here has its key dropped with a `⚠`, and a metric whose tool ran and failed aborts the write entirely, because a floor recorded from a broken run is worse than no floor.
- Arch: `go run harness.go arch` — go-arch-lint against `.go-arch-lint.yml`
- Arch config guard: `go run harness.go arch-config-guard` — blocks unreviewed `.go-arch-lint.yml` changes in pre-commit/pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Gherkin-first guard: `go run harness.go gherkin-guard` — mechanizes the Gherkin-first rule below. Triggers when at least one changed "production source" file (a non-test `.go` file outside `features/`, excluding `harness.go` itself) has no changed path ending in `.feature`. Silently passes (no output) when the template has no `.feature` files anywhere yet — retrofitting the harness into a repo with no acceptance suite must never block. Modes mirror the arch config guard exactly: `--warn`; `--staged`; env override `HARNESS_ALLOW_NO_FEATURE=1`. WARN in `check`/`stop-hook`; BLOCK in `pre-commit` (staged), `pre-push` (+ pre-push stdin refs), and `ci`.
- Agents drift: `go run harness.go agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `go run harness.go sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `go run harness.go setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and idempotently installs the Claude/Codex Stop wiring
- Stop hook: auto-formats/fixes changed files, then runs complexity (`stop-hook`). On failure it exits **2** and prints a short failure summary to stderr — Claude Code treats a Stop hook's exit code 2 as blocking and feeds stderr back to the model; any other non-zero exit is silently swallowed. `check`/`ci`/`pre-commit`/`pre-push` keep exiting 1 on failure. Codex's wrapper (`codex-stop-hook.sh`) maps any non-zero exit to a block decision, so it is unaffected by the exit-code change.

## Definition of done

- `go run harness.go check` passes clean — never stop with check failing.
- User-visible behavior change → a `.feature` scenario exists and acceptance passes. Mechanically enforced by the Gherkin-first guard (`gherkin-guard`): `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail unless a `.feature` changed alongside the production source, or `HARNESS_ALLOW_NO_FEATURE=1` is set after review.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- `pre-push`/`ci` are the human's gates: leave the tree in a state where they would pass, but do not commit or push yourself.

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
</important>

<important if="the task changes user-visible behavior">
- Workflow: write or extend a `.feature` scenario under `features/` → get human approval → write step definitions under `features/steps/` → write implementation.
- Mechanically enforced: `go run harness.go gherkin-guard` blocks a changed production `.go` file (outside `features/`, excluding `harness.go` itself) with no changed `.feature` alongside it — see Commands.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a rapid property test, not just examples — see `crap/properties_test.go` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `.go-arch-lint.yml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The harness warns about `.go-arch-lint.yml` changes during `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
</important>
