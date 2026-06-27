# CLAUDE

## Commands

- After edits: `cargo harness check` — fix, format, lint, test, suppression report
- Pre-commit: `cargo harness pre-commit` — staged files only (auto via git hook)
- Pre-push: `cargo harness pre-push` — read-only push gate over the whole tree: clippy, format check, acceptance, arch (the offline checks pre-commit and stop-hook skip; runs them in parallel). Auto via git pre-push hook.
- CI: `cargo harness ci` — read-only gates (clippy, format check, complexity, acceptance, arch) run in parallel — captured, printed in submission order, run to completion — then audit, tests + coverage (stream), crap. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `cargo harness complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests
- Deadcode: no separate target — rust's `dead_code` lint is on by default and `ci`'s strict clippy (`-D warnings`) denies unused functions, fields, and variants.
- CRAP (advisory): `cargo harness crap --max=30` — complexity × coverage gate (joins lizard --csv with `target/llvm-cov/lcov.info`). Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Audit: `cargo harness audit` — audit dependencies for known vulnerabilities (via cargo-audit)
- Acceptance: `cargo harness acceptance` — run cucumber against `tests/features/`
- Coverage: `cargo harness coverage --min=0` — cargo-llvm-cov line coverage with threshold
- Mutation (advisory): `cargo harness mutation` — cargo-mutants kill-rate on the crate
- Arch: `cargo harness arch` — cargo-modules checks against `arch.toml`
- Agents drift: `cargo harness agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `cargo harness sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `cargo harness setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and verifies the Claude/Codex Stop wiring (the runner is std-only — it checks rather than rewrites JSON that carries other hooks; copy the template's `.claude`/`.codex` if it warns)
- Stop hook: auto-formats/fixes changed files, then runs complexity and CRAP (`stop-hook`)

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
- Step definitions are Rust functions in `tests/acceptance.rs`; the `.feature` files live under `tests/features/`.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a proptest property test, not just examples — see `mod property_tests` in `harness.rs` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `arch.toml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The `PreToolUse` hook denies edits to `arch.toml` unless the user's current prompt explicitly authorized it.
</important>
