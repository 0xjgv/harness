# Remove Claude behavior hooks while keeping arch-config protection

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

## Purpose / Big Picture

The templates currently ship Claude-only behavior hooks that classify prompts, block
commits, reprint role instructions, and deny some edits before tool execution. This
change removes that prompt-approval machinery while preserving the important safety
property: architecture configuration changes should not slip into commits, pushes, or
CI unnoticed. After the change, the templates still run Stop hooks for formatting and
complexity/deadcode checks, but arch-config protection moves into normal harness
commands as an `arch-config-guard`.

## Progress

- [x] (2026-07-09) Read PLANS.md and inspect current hook/docs/runner surfaces.
- [x] (2026-07-09) Remove `.claude/scripts` behavior hooks and simplify `.claude/settings.json`.
- [x] (2026-07-09) Add `arch-config-guard` to Python, Bun, Go, Rust, and monorepo harnesses.
- [x] (2026-07-09) Update AGENTS.md/CLAUDE.md, README files, and skill references.
- [x] (2026-07-09) Sync generated AGENTS.md copies and deployed skill references.
- [x] (2026-07-09) Run repo checks and targeted template checks.

## Surprises & Discoveries

- Observation: The Rust runner is std-only and already verifies Stop hook wiring rather
  than rewriting JSON.
  Evidence: `rust/harness.rs` `cmd_hooks()` installs git hooks and then calls
  `check_stop_hook_present()`.
- Observation: The monorepo root delegates almost everything to subprojects, so its
  arch-config guard must check root and nested arch config paths itself.
  Evidence: `monorepo/Makefile` implements root-level `agents-md-drift` directly but
  otherwise dispatches commands to subproject runners.
- Observation: When these templates are edited inside this parent repository, Git
  reports paths like `rust/arch.toml`, not `arch.toml`, even when commands run from
  `rust/`.
  Evidence: `git status --porcelain --untracked-files=no -- .` from `rust/` printed
  `M rust/arch.toml`.

## Decision Log

- Decision: Remove mechanical commit/push denial entirely.
  Rationale: Local commits are recoverable and push safety belongs in pre-push, CI,
  review, and branch protection. Prompt-classifier state adds friction that defeats
  agent autonomy.
  Date/Author: 2026-07-09 / Codex.
- Decision: Make arch-config protection a harness gate, not a pre-edit hook.
  Rationale: Without pre-tool hooks, edits cannot be blocked before they happen, but
  they can be blocked before integration through pre-commit, pre-push, and CI.
  Date/Author: 2026-07-09 / Codex.
- Decision: Use `HARNESS_ALLOW_ARCH_CONFIG=1` as the explicit override.
  Rationale: It is narrow, scriptable, easy to audit in logs, and applies consistently
  across language runners.
  Date/Author: 2026-07-09 / Codex.
- Decision: Scope language-template guard diffs to `.` and normalize paths with
  `git rev-parse --show-prefix`.
  Rationale: The guard must protect exact project-local config files whether the
  template is a standalone repository or a subdirectory of a larger worktree.
  Date/Author: 2026-07-09 / Codex.

## Outcomes & Retrospective

The Claude-only prompt/session/pre-tool behavior hooks were removed from all five
templates. Claude settings now wire only Stop hooks, matching the mechanical surface
Codex already had.

Architecture config protection moved into harness commands:

- `check` and `stop-hook` warn.
- `pre-commit`, `pre-push`, and `ci` fail unless `HARNESS_ALLOW_ARCH_CONFIG=1`.
- Python, Bun, Go, Rust, and monorepo expose `arch-config-guard`.

The current diff intentionally changes `rust/arch.toml` to remove a stale hook
comment, so strict guard commands reject it until the explicit override is used. That
is the intended behavior for protected architecture config changes.

## Context and Orientation

The repository contains language templates under `python/`, `bun/`, `go/`, `rust/`,
and `monorepo/`. Each single-language template has a runner (`harness.py`,
`harness.ts`, `harness.go`, or `harness.rs`) and documentation files `CLAUDE.md` and
`AGENTS.md`. The files `AGENTS.md` and `CLAUDE.md` are intended to be byte-identical
inside each template. The monorepo root uses `monorepo/Makefile`.

Layer 1 means quality checks: `check`, `pre-commit`, `pre-push`, `ci`, `audit`,
`post-edit`, and `stop-hook`. Layer 2 currently means Claude-only behavior hooks under
`.claude/scripts`. This plan removes the Claude-only Layer 2 scripts and replaces
arch-config write protection with `arch-config-guard`.

Protected architecture config paths are:

- Python: `.importlinter`
- Bun: `.dependency-cruiser.json`
- Go: `.go-arch-lint.yml`
- Rust: `arch.toml`
- Monorepo: all four names anywhere under the repository

## Plan of Work

First, simplify every template `.claude/settings.json` so it only wires the `Stop`
hook, matching Codex's current mechanical surface. Delete the four behavior scripts
from every template.

Second, add an `arch-config-guard` command to each runner. The guard detects changed
protected config paths using git. It should compare against staged files in
`pre-commit` and against working-tree/untracked files in `check`, `stop-hook`,
`pre-push`, and `ci`. In warning mode it prints a warning but does not fail. In strict
mode it fails unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set. The command itself runs in
strict mode by default and accepts `--warn` for advisory mode.

Third, wire the guard into command stages. `check` and `stop-hook` use warning mode,
because agent edits should be allowed while still surfaced. `pre-commit`, `pre-push`,
and `ci` use strict mode so the change cannot integrate silently.

Fourth, update docs and skill references to describe an integration guard instead of
prompt-classifier hooks. Keep written guidance that agents should not commit or push
unless asked, but remove claims that hooks deny those commands.

## Concrete Steps

All commands run from `/Users/juan/Code/harness-templates`.

After edits, run:

    make sync-skills && make check

Then run targeted checks that do not require external services where practical:

    uv run --project python harness arch-config-guard --warn
    bun bun/harness.ts arch-config-guard --warn
    cd go && go run harness.go arch-config-guard --warn
    cd rust && cargo harness arch-config-guard --warn

## Validation and Acceptance

The change is accepted when template `.claude/settings.json` files contain only Stop
hook wiring, no template contains `.claude/scripts/pre-bash-gate.sh`,
`.claude/scripts/pre-edit-gate.sh`, `.claude/scripts/session-start.sh`, or
`.claude/scripts/ups-classify.sh`, each runner exposes `arch-config-guard`, and
`make check` passes after syncing skills.

Manual behavior: if a protected arch config is changed, `check` and `stop-hook` warn,
while `pre-commit`, `pre-push`, and `ci` fail unless run with
`HARNESS_ALLOW_ARCH_CONFIG=1`.

## Idempotence and Recovery

The edits are file-based and safe to repeat. `make sync-skills` is idempotent. If a
guard blocks a legitimate arch config change, rerun the integration command with
`HARNESS_ALLOW_ARCH_CONFIG=1` after human review.

## Artifacts and Notes

- `make sync-skills`: passed; synced `skills/harness` to `/Users/juan/.claude/skills/harness`
  and `/Users/juan/.agents/skills/harness`.
- `make check`: passed; skill drift check reports canonical files match both deployed
  targets.
- `uv run harness check` from `python/`: passed; quality checks, Stop hook wiring,
  arch-config guard, `agents-md-drift`, and suppression baseline passed. The command
  also emitted the existing Stop-hook suppression warning before the quality block.
- `bun harness.ts check` from `bun/`: passed; lockfile sync, fix/format, typecheck,
  tests, Stop hook wiring, arch-config guard, `agents-md-drift`, and suppression
  baseline passed. Biome still reports existing `noConsole` warnings when run directly
  with `--write`.
- `go run harness.go check` from `go/`: passed.
- `cargo harness check` from `rust/`: passed and warned that `arch.toml` changed.
- `make check` from `monorepo/`: passed and warned that `rust/arch.toml` changed.
- `cargo harness arch-config-guard` from `rust/`: failed as expected on `arch.toml`.
- `HARNESS_ALLOW_ARCH_CONFIG=1 cargo harness arch-config-guard` from `rust/`: passed.
- `make arch-config-guard` from `monorepo/`: failed as expected on `rust/arch.toml`.
- `HARNESS_ALLOW_ARCH_CONFIG=1 make arch-config-guard` from `monorepo/`: passed.
- Final stale-reference scan found only the intentional reference-doc warnings that
  say not to add `SessionStart`, `UserPromptSubmit`, or `PreToolUse` behavior gates.
- `find python bun go rust monorepo -path '*/.claude/scripts/*' -type f`: no output.
- `git diff --check`: passed.
