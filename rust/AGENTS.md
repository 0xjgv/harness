# CLAUDE

## Commands

- After edits: `cargo harness check` — fix, format, lint, test (the `acceptance` `[[test]]` target runs under plain `cargo test`, so this already covers Gherkin/cucumber scenarios — see the Acceptance bullet below), complexity, arch-config-guard (warn), Gherkin-first guard (warn), suppression ratchet. **Invariant: `check` runs every gate that is offline, fast, and takes no build lock.** `ci` adds: the dependency audit (network), coverage, CRAP (advisory) — and, uniquely in this template, the `arch` gate: `cargo modules` builds and takes cargo's exclusive lock on `target/`, so it stays `ci`/`pre-push`-only rather than serializing against `check`'s other cargo invocations.
- Pre-commit: `cargo harness pre-commit` — the arch-config guard and the gherkin-first guard run first, staged-mode, before checking whether any Rust files are staged, so a commit that stages only an arch config edit (or any other non-Rust file) still gets blocked. Only then: staged files (auto via git hook) get fix/format, which rewrite the working tree, then the fixed files are re-staged (`git add`) so the commit records the fixed content. Caveat: if a file is only partially staged, `git add` also stages its remaining unstaged hunks.
- Pre-push: `cargo harness pre-push` — read-only push gate over the whole tree: format check runs standalone (it never touches cargo's build lock), then clippy (strict, `--locked`), acceptance, and arch run sequentially — each takes cargo's exclusive lock on `target/`, so running them concurrently would only serialize behind it while interleaving captured output. The offline checks pre-commit and stop-hook skip. Auto via git pre-push hook.
- CI: `cargo harness ci` — format check + complexity run in parallel (neither touches cargo's build lock); clippy (strict, `--locked`), acceptance, and arch then run sequentially for the same lock-contention reason — captured, printed in submission order, run to completion — then audit, tests (skipped when cargo-llvm-cov is installed, since coverage already runs the suite once) + coverage (stream), crap. `--locked` on the strict clippy gate also fails ci/pre-push if `Cargo.lock` is stale. CRAP is advisory (warns only — pass `--enforce` to hard-fail). Requires `uvx` on PATH.
- Complexity: `cargo harness complexity` — lizard@1.22.2 CC gate (CCN≤15, args≤8, length≤100) over src + tests
- Deadcode: no separate target — rust's `dead_code` lint is on by default and `ci`'s strict clippy (`-D warnings`) denies unused functions, fields, and variants; unused dependencies surface via `cargo`'s own warnings (or `cargo-machete`).
- CRAP (advisory): `cargo harness crap --max=30` — complexity × coverage gate (joins lizard --csv with `target/llvm-cov/lcov.info`). Add `--enforce` to exit 1 on offenders (default exits 0 with warning).
- Audit: `cargo harness audit` — audit dependencies for known vulnerabilities (via cargo-audit)
- Acceptance: `cargo harness acceptance` — run cucumber against `tests/features/`. Not a separate gate in `check`: the `acceptance` test is a Cargo `[[test]]` target (`tests/acceptance.rs`, `harness = false`), so plain `cargo test` already executes it as part of `check`'s "Tests" step — unlike python/bun/go, whose acceptance runner is a standalone script their default test command does not invoke automatically, so those templates need `check` to call it separately.
- Coverage: `cargo harness coverage --min=0` — cargo-llvm-cov line coverage with threshold; default comes from `.harness-baseline` `coverage.min`; a non-integer `--min=` value prints an error and exits 1
- Mutation (advisory): `cargo harness mutation` — cargo-mutants kill-rate on the crate
- Suppressions: `cargo harness suppressions` — full suppression breakdown; `--update-baseline` requires human sign-off and updates `.harness-baseline`
- Arch: `cargo harness arch` — cargo-modules checks against `arch.toml`
- Arch config guard: `cargo harness arch-config-guard` — blocks unreviewed `arch.toml` changes in pre-commit/pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review. Base ref for the diff: `HARNESS_ARCH_BASE` env, else `GITHUB_BASE_REF` (only set on `pull_request` events), else the first of `origin/HEAD`/`origin/main`/`main` that resolves — so a direct push to `main` (no PR event) still gets checked; silently skipped if none of those resolve.
- Gherkin guard: `cargo harness gherkin-guard` — mechanizes the Gherkin-first rule below. Blocks when a changed "production source" file (a `.rs` file under `src/`, excluding `tests/` and excluding `harness.rs` itself) has no matching changed `.feature` file, in pre-commit (staged)/pre-push (incl. pre-push stdin refs)/CI; warns only in `check`/`stop-hook`. Use `HARNESS_ALLOW_NO_FEATURE=1` after review to override. Skips entirely (silent pass) when the template has no `.feature` files anywhere yet — retrofitting into a repo without an acceptance suite must never block.
- Agents drift: `cargo harness agents-md-drift` — fail if AGENTS.md differs from CLAUDE.md
- Sync: `cargo harness sync-agents-md` — overwrite AGENTS.md from CLAUDE.md
- Setup: `cargo harness setup-hooks` installs git pre-commit + pre-push hooks (path resolved via `git rev-parse`, worktree-safe) and verifies the Claude/Codex Stop wiring (the runner is std-only — it checks rather than rewrites JSON that carries other hooks; copy the template's `.claude`/`.codex` if it warns)
- Stop hook: auto-formats/fixes changed files, then runs complexity (`stop-hook`). On failure it exits **2** and writes a short `Stop hook failed: <gate names>` summary to stderr — Claude Code treats exit 2 as blocking and feeds stderr back to the model (any other exit code is silently swallowed); Codex's wrapper already turns any non-zero exit into a block.

## Definition of done

- `cargo harness check` passes clean — never stop with check failing.
- User-visible behavior change → a `.feature` scenario exists and acceptance passes; mechanically enforced by `cargo harness gherkin-guard` once the template has at least one `.feature` file.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- Gherkin-first is integration-blocked the same way: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail when a production-source file changed with no `.feature` file, unless `HARNESS_ALLOW_NO_FEATURE=1` is set after review.
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
- Step definitions are Rust functions in `tests/acceptance.rs`; the `.feature` files live under `tests/features/`.
- Mechanically enforced by `cargo harness gherkin-guard`: a changed "production source" file (a `.rs` file under `src/`, excluding `tests/` and excluding `harness.rs` itself) with no changed `.feature` file blocks `pre-commit`/`pre-push`/`ci` (warns in `check`/`stop-hook`); `HARNESS_ALLOW_NO_FEATURE=1` overrides after review. `harness.rs` is excluded from "production source" — the harness is tooling, not product behavior.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a proptest property test, not just examples — see `mod property_tests` in `harness.rs` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `arch.toml` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The harness warns about `arch.toml` changes during `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
</important>
