# CLAUDE

## Commands

- After edits: `cargo harness check` — fix, format, lint, test, suppression ratchet
- Pre-commit: `cargo harness pre-commit` — runs when Rust files are staged (auto via git hook): clippy fix + format. A staged `CLAUDE.md` is copied to `AGENTS.md` and staged with it; a hand edit to `AGENTS.md` alone fails. Staged `arch.toml` changes warn, they do not fail. Tests run at pre-push.
- Pre-push: `cargo harness pre-push` — branch guard runs first and short-circuits (refuses pushes to main/master unless `HARNESS_ALLOW_PROTECTED_PUSH=1`, printing only the refusal and exiting before anything else runs); then the arch config guard, then agents-md-drift as a separate hard check, then the test suite (alone, without git's `GIT_*` hook variables, because it writes `target/`), then a read-only push gate over the whole tree: clippy, format check, acceptance, arch, run in parallel (the offline checks pre-commit and stop-hook skip). Auto via git pre-push hook.
- CI: `cargo harness ci` — read-only gates (clippy, format check, complexity, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then agents-md-drift as a separate hard check, then audit, tests + coverage (stream), crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `cargo harness complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests
- Deadcode: no separate target — rust's `dead_code` lint is on by default and `ci`'s strict clippy (`-D warnings`) denies unused functions, fields, and variants; unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`). At stop, `dead_code` on changed lines arrives through the stop hook's lint.
- CRAP (advisory): `cargo harness crap --max=30` — complexity × coverage gate (joins lizard --csv with `target/llvm-cov/lcov.info`). Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Audit: `cargo harness audit` — audit dependencies for known vulnerabilities (via cargo-audit)
- Acceptance: `cargo harness acceptance` — run cucumber against `tests/features/`
- Coverage: `cargo harness coverage --min=0` — cargo-llvm-cov line coverage with threshold; default comes from `.harness-baseline` `coverage.min`
- Mutation (advisory): `cargo harness mutation` — cargo-mutants kill-rate on the crate
- Suppressions: `cargo harness suppressions` — full suppression breakdown; `--update-baseline` requires human sign-off and updates `.harness-baseline`
- Arch: `cargo harness arch` — cargo-modules checks against `arch.toml`
- Branch guard: `cargo harness branch-guard` — refuses pushes to (or deletions of) `main`/`master`; reads `HARNESS_PRE_PUSH_REFS`, else git pre-push stdin (1s deadline; partial input fails), else the current branch; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides
- Arch config guard: `cargo harness arch-config-guard` — warns in check/pre-commit, blocks pre-push/CI; `--pre-push` also inspects the push refs; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Agents drift: `cargo harness agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `cargo harness sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `cargo harness setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and verifies the Claude/Codex Stop wiring and the Claude PostToolUse wiring (the runner is std-only — it checks rather than rewrites JSON that carries other hooks; copy the template's `.claude`/`.codex` if it warns)
- Stop hook: `cargo harness stop-hook` — post-edit, then changed-lines lint, complexity delta; silent on success, exit 2 with findings. Changed lines = `git diff` against the merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then origin/HEAD, origin/main, origin/master, main, master; never fetched) plus untracked files. Lint = clippy's JSON diagnostics (warnings and errors, rustc's included) whose primary span sits on a changed line. A function blocks only when it is over a lizard limit and new or worse than at the base (`src/` + `tests/`); pre-existing debt never blocks. Findings go to stderr (at most 20 lines; `--verbose` lifts the cap); exit 1 means a tool could not run (a build that fails away from the changed lines included), or the same findings came back while the agent was already continuing from a stop (loop guard). An uncommitted `CLAUDE.md` edit is copied to `AGENTS.md`. Whole-tree gates stay in check/pre-push/ci.
- Runner: `cargo harness` is a `.cargo/config.toml` alias that runs the `harness` bin with `build.warnings='allow'`. The bin depends on the crate's lib, so cargo would otherwise replay every cached lib warning ahead of each hook's output; ci's strict clippy stays the gate. Older cargo ignores the key and prints those warnings (1.96 does; 1.98 honors it).
- Post-edit: `cargo harness post-edit` — rustfmt on `.rs` files with uncommitted changes, never on untouched child modules; `cargo clippy --fix` rewrites the whole crate, so it stays in `check`/`fix`/`pre-commit`. `--hook` (Claude PostToolUse) formats the one edited file and, when it changed, prints an `additionalContext` line asking the agent to re-read it; it never blocks.

## Definition of done

- `cargo harness check` passes clean — never stop with check failing.
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
- Law-like behavior also gets a proptest property test, not just examples — see `mod property_tests` in `harness.rs` for the pattern.
- Small or incidental behavior changes may ship on unit tests alone; say so in your first response. Refactors, typo fixes, dependency bumps, and internal cleanup are not behavior changes at all.
- If it is unclear which bucket a task falls in, state your classification and proceed.
</important>

<important if="you want to edit `arch.toml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — put the config change in its own commit whose message states the rationale, so the reviewer sees it isolated.
- The harness warns about `arch.toml` changes during `check`/`pre-commit` and blocks `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Expect the push to be refused; report it and let the human review.
</important>
