# CLAUDE

## Workspace

`make workspace` is the clean-clone and new-VM entry point. It supports macOS and
glibc Linux on `x86_64` and `arm64`. The bootstrap layer is Make, Bash, Git, curl,
tar, Info-ZIP unzip, a SHA-256 utility, and a writable `HOME`. It requires a Git
worktree with clean tracked/index state; untracked files are preserved.

Workspace preflights the platform, bootstrap commands, lock manifest, checksums,
Git state, Stop configuration, skill source, and both Git-hook destinations before
downloading or modifying hooks or skills. It does not use `sudo`, Homebrew, or shell
profile edits. Ambient Python and tool versions are ignored. Exact managed tools
live under `~/.local/share/harness/tools/<tool>/<version>`. Pins are uv 0.12.5,
CPython 3.13.15, lizard 1.22.2, vulture 2.16, and pip-audit 2.10.1.

After tools and locked dependencies are installed, workspace deploys skills, installs
the root Git hooks, verifies the checked-in Claude/Codex Stop wiring, runs the normal
auto-fixing `make check`, and verifies tracked/index state again. If that check changes
tracked files, workspace fails with their paths and does not revert them.

`make workspace OFFLINE=1` makes no network requests and succeeds only after an
online run has warmed the exact tool and dependency caches. A cold or incomplete
offline cache fails before hooks or skills are modified. `make bootstrap` is a
compatibility alias for `make workspace`.

`make deps` restores the committed graph with `uv sync --locked`;
`make deps OFFLINE=1` adds `--offline`. Upgrades remain explicit and reviewed:

```bash
.harness/workspace.sh exec python -- uv lock --upgrade
git diff -- uv.lock
make deps
```

## Commands

Make is the normal managed boundary:

- After edits: `make check` — fix, format, typecheck, test (or syntax check when no tests exist), suppression ratchet
- Pre-commit: `make pre-commit` — staged files only (auto via Git hook)
- Pre-push: `make pre-push` — read-only push gate over the whole tree: lint, format check, acceptance, and arch (the offline checks pre-commit and stop-hook skip), in parallel; runs automatically via the Git pre-push hook
- CI: `make ci` — read-only lint, format, typecheck, audit, complexity, deadcode, acceptance, and arch gates run in parallel, are captured and printed in submission order, and run to completion; coverage then streams and CRAP remains advisory
- Complexity: `make complexity` — managed lizard 1.22.2 CC gate (CCN≤15, args≤8, length≤100) over `src/` and `tests/`
- Deadcode: `make deadcode` — managed vulture 2.16 over `src/` only (`--min-confidence 60`), so a dead helper that still has a test surfaces rather than hides; allowlist genuine dynamic references in `vulture_allowlist.py`; runs in CI and the Stop hook
- Audit: `make audit` — managed pip-audit 2.10.1 vulnerability audit
- Acceptance: `make acceptance` — run behave against `tests/features/`
- Coverage: `make coverage` — coverage.py with the `.harness-baseline` threshold and uncovered listing; warns and skips when no `tests/test*.py` files exist
- Mutation: `make mutation` — advisory mutmut kill-rate over `src/`; warns and skips when no tests exist
- CRAP: `make crap` — advisory complexity × coverage gate with max 30; use the managed runner's `--enforce` flag to hard-fail, and expect a warning/skip when no tests exist
- Suppressions: `make suppressions` — full suppression breakdown; updating `.harness-baseline` requires the managed runner's `--update-baseline` flag and human sign-off
- Arch: `make arch` — import-linter against `.importlinter`
- Arch config guard: `make arch-config-guard` — blocks unreviewed `.importlinter` changes in pre-commit/pre-push/CI; use `HARNESS_ALLOW_ARCH_CONFIG=1` after review
- Agents drift: `make agents-md-drift` — fail if `AGENTS.md` differs from `CLAUDE.md`
- Sync: `make sync-agents-md` — overwrite `AGENTS.md` from `CLAUDE.md`
- Setup: `make setup-hooks` — delegate collision-safe Git-hook installation and Stop verification to the provisioner
- Stop hook: `make stop-hook` — auto-format/fix changed files, then run complexity and deadcode in parallel

When harness-specific flags are required, use the exact managed runner boundary:

```bash
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness check --verbose
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness coverage --min=80
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness crap --enforce
.harness/workspace.sh exec python -- uv run --frozen --no-sync harness suppressions --update-baseline
```

Keep single tests and direct analyzer diagnostics inside the same profile:

```bash
.harness/workspace.sh exec python -- uv run --frozen --no-sync python -m unittest tests.test_crap
.harness/workspace.sh exec python -- lizard src tests -C 15 -a 8 -L 100 -i 0
.harness/workspace.sh exec python -- vulture src vulture_allowlist.py --min-confidence 60
```

Only `.harness/workspace.sh install-hooks` writes hooks. Unknown existing Git hooks
cause preflight to fail without modifying either hook; only exact legacy harness shims
are migrated. Installed Git hooks and the checked-in Stop hooks enter the Git root and
call `make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the
managed environment. CI uses the same two commands locally and remotely:

```bash
make workspace
make ci
```

## Definition of done

- `make check` passes clean — never stop with check failing.
- User-visible behavior change → a `.feature` scenario exists and acceptance passes.
- No new suppressions: additions above `.harness-baseline` fail check; suppress only with the human's sign-off, stating why.
- Arch config changes are integration-blocked: `check`/`stop-hook` warn, and `pre-commit`/`pre-push`/`ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- `make pre-push` and `make ci` remain green and read-only; do not commit or push unless the current user prompt authorizes it.

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
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a Hypothesis property test, not just examples — see `tests/test_properties.py` for the pattern.
- Refactors, typo fixes, dependency bumps, and internal cleanup are NOT user-visible behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible behavior, ASK before editing source.
</important>

<important if="you want to edit `.importlinter` (arch config)">
- Do not silently edit the arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The harness warns about `.importlinter` changes during `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
</important>
