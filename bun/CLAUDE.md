# CLAUDE

## Commands

All harness commands are exposed as `bun run <name>` package.json scripts (each forwards
to `bun harness.ts <name>`), except `arch-config-guard`, which has no script and is run
as `bun harness.ts arch-config-guard`.

**Invariant: `check` runs every gate that is offline, fast, and takes no build lock.**
Arch (dependency-cruiser) qualifies — it's a local devDependency, runs offline, and
takes no build lock — so it joins the read-only parallel batch here too. `ci` adds
only: the dependency audit (network), coverage, and CRAP (advisory). If you add a new
offline, no-build-lock gate to `ci`, add it to `check` too.

- After edits: `bun run check` — lockfile sync (`bun install --frozen-lockfile`), fix, format, typecheck, test (warns/skips when no tests exist), complexity, deadcode, acceptance, arch (all four run as a read-only parallel batch after the fix step), hook-drift + arch-config-guard (warn) + gherkin-guard (warn) + suppression ratchet
- Pre-commit: `bun run pre-commit` — the arch-config guard and the gherkin-guard run first, staged-mode, before checking whether any TypeScript files are staged, so a commit that stages only an arch config edit (or any other non-TypeScript file) still gets blocked. Only then: staged files (auto via git hook) get fixed, then re-staged so the commit records the fixed content, not the pre-fix blob. Caveat: for a partially staged file, `git add` also stages its unstaged hunks (same trade-off lint-staged makes).
- Pre-push: `bun run pre-push` — read-only push gate over the whole tree: lint (biome covers format), acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `bun run ci` — read-only gates (lint, typecheck, audit, complexity, deadcode, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then coverage (streams) + crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `bun run complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests. The violation count is ratcheted: lizard runs with `-i <complexity.max_violations>` from `.harness-baseline`, so it fails only when more functions are flagged than the recorded floor. With no file or key it runs report-only: passes, labelled `report-only: no .harness-baseline floor`, and hints to run `suppressions --update-baseline`.
- Deadcode: `bun run deadcode` — knip (via bunx, no devDep) flags unused files/exports/deps; `knip.json` lists the cucumber step entries and ignores tool devDeps invoked as binaries. Runs in ci + stop-hook.
- Audit: `bun run audit` — audit dependencies for known vulnerabilities (via `bun audit`)
- Acceptance: `bun run acceptance` — run cucumber against `tests/features/`
- Coverage: `bun run coverage --min=0` — `bun test` coverage (LCOV) with threshold; default comes from `.harness-baseline` `coverage.min`; warns and skips when no tests exist; `--min=` must be an integer or the command errors out instead of silently passing
- Mutation (advisory): `bun run mutation` — Stryker mutation score on src/; warns and skips when no tests exist
- CRAP (advisory): `bun run crap --max=30` — complexity × coverage gate. Offenders above `--max` are compared to the `crap.max_violations` floor in `.harness-baseline`: ✓ while the count stays at or below the floor, warn above it. Add `--enforce` to exit 1 above the floor (default exits 0 with warning). With no file or key it runs report-only — offenders are listed, the line reads `report-only: no .harness-baseline floor`, and it exits 0 even under `--enforce`. Warns and skips when no tests or coverage artifact exist.
- Suppressions: `bun run suppressions` — full suppression breakdown (locations are always reported relative to the template root); `--update-baseline` requires human sign-off and rewrites `.harness-baseline` by merging: it re-measures every ratcheted key (`coverage.min`, `complexity.max_violations`, `crap.max_violations`, and `suppressions.<kind>` — a vanished kind is recorded as 0), drops a key it cannot measure here (with a warning, so a shipped floor never leaks into an adopting repo), preserves keys it does not own (e.g. `mutation.min`), and writes nothing at all if any measurement errors.
- Arch: `bun run arch` — dependency-cruiser against `.dependency-cruiser.json`
- Arch config guard: `bun harness.ts arch-config-guard` — blocks unreviewed `.dependency-cruiser.json` changes in pre-commit/pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review. On CI push events (no `GITHUB_BASE_REF`), the base-diff check falls back to the first ref that resolves among `origin/HEAD`, `origin/main`, `main`.
- Gherkin-first guard: `bun run gherkin-guard` — mechanizes "write a `.feature` before changing user-visible behavior". Triggers when a changed file is under `src/` (excluding `tests/`) — deliberately not `harness.ts` and not the test tree itself, so the scope is predictable — and no changed path ends in `.feature`. **Skips entirely and silently** when the template has no `.feature` files anywhere (adoption path: retrofitting the harness into a repo with no acceptance suite must never block). `--warn` for advisory mode, `--staged` for staged paths only, `HARNESS_ALLOW_NO_FEATURE=1` to override after review. WARN in `check`/`stop-hook`; BLOCK in `pre-commit` (staged)/`pre-push` (incl. pre-push stdin refs)/`ci`.
- Agents drift: `bun run agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `bun run sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `bun run setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and idempotently installs the Claude/Codex Stop wiring
- Post-edit: `bun run post-edit` — format/fix files with uncommitted changes in this template's subtree; used internally by `stop-hook` and directly by agents after edits
- Stop hook: `bun run stop-hook` — auto-formats/fixes changed files, warns via `arch-config-guard` and `gherkin-guard`, then runs complexity and deadcode in parallel. On failure it exits 2 and writes a short summary to stderr (which Claude Code's Stop hook treats as blocking and feeds back to the model); Codex's wrapper (`.codex/hooks/codex-stop-hook.sh`) turns any non-zero exit into a block regardless.

## Definition of done

- `bun run check` passes clean — never stop with check failing.
- User-visible behavior change → a `.feature` scenario exists and acceptance passes.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Ratcheted floors (`complexity.max_violations`, `crap.max_violations`, `coverage.min`, `suppressions.*`) only move down: regenerate with `bun run suppressions --update-baseline`, never hand-raise a number.
- Arch config changes are integration-blocked: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- Gherkin-first is integration-blocked the same way: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail on a `src/` change with no `.feature` change, unless `HARNESS_ALLOW_NO_FEATURE=1` is set after review — see `gherkin-guard` above.
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
- Workflow: write or extend a `.feature` scenario → get human approval → write step definitions → write implementation.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a fast-check property test, not just examples — see `tests/properties.test.ts` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
- This is mechanically enforced by `gherkin-guard`: a `src/` change with no matching `.feature` change warns in `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci`.
</important>

<important if="you want to edit `.dependency-cruiser.json` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The harness warns about `.dependency-cruiser.json` changes during `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
</important>
