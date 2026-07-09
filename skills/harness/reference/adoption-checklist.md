# adoption-checklist

Use this when auditing a repo that already has, or claims to have, a
harness implementation. The goal is to find missing stages, incorrect
substages, noisy output, and incomplete hook/docs wiring before changing code.

This checklist covers **complete harness adoption**: Layer 1 quality harness,
Layer 2 behavior contract, Stop hook wiring, AGENTS.md/CLAUDE.md drift
protection, `arch-config-guard`, and the supporting gates (`pre-push`,
`stop-hook`, deadcode where applicable, CRAP, property-based tests,
suppression ratchets).

For brownfield repos, audit complete adoption, but keep remediation explicit.
If the user declines Layer 2 behavior instructions or the arch-config guard,
record it as "skipped by decision", not as an unexamined gap.

## Read first

Do not audit from memory. Read:

1. `~/Code/harness-templates/README.md`
2. `~/Code/harness-templates/skills/harness/SKILL.md`
3. The language reference for the repo:
   - Python: [python.md](python.md)
   - Bun: [bun.md](bun.md)
   - Go: [go.md](go.md)
   - Rust: [rust.md](rust.md)
   - Monorepo: [monorepo.md](monorepo.md)
4. Hook shape: [settings-json.md](settings-json.md)
5. Behavior contract: [behavior-contract.md](behavior-contract.md)

## Classify the repo

Record one mode:

- **Greenfield**: empty or disposable starting point. Expected remediation is
  to copy the matching template, then verify every item below.
- **Brownfield**: existing code, runner, CI, tests, or docs. Expected
  remediation is to preserve the existing runner and add missing semantics.
- **Monorepo**: two or more subprojects. Expected remediation is root
  dispatch plus per-subproject harnesses; the root must not reimplement
  language-specific logic.

Record the runner:

- Existing runner: `make`, `just`, `npm`, `bun`, `cargo`, `go run`, custom.
- Harness entrypoint: the command prefix used for examples, such as
  `uv run harness`, `bun harness.ts`, `go run harness.go`, `cargo harness`,
  or `make`.
- If the repo already has a runner, do not recommend adding a second one
  unless the existing runner cannot express the harness contract.

## Severity

Use these labels in the report:

- **Required**: part of complete harness adoption and should be fixed.
- **Strongly recommended**: part of complete adoption, but can be staged if the
  repo has a documented constraint.
- **Contextual**: valuable when the repo has the matching surface area or tool,
  or when the user accepts the extra cost.
- **Skipped by decision**: consciously not adopted after user direction.

Default classification:

| Capability | Severity |
|---|---|
| `check` command | Required |
| `pre-commit` command + installed git hook | Required |
| `pre-push` command + installed git hook | Required |
| `ci` command | Required |
| `audit` command | Required |
| `post-edit` command | Required |
| `stop-hook` command + Claude/Codex Stop wiring | Required |
| Quiet runner output contract | Required |
| Read-only `ci` and `pre-push` | Required |
| Suppression ratchet via `.harness-baseline` | Required |
| Coverage floor via `.harness-baseline` | Required |
| AGENTS.md/CLAUDE.md full-content parity | Required |
| `agents-md-drift` and `sync-agents-md` | Required |
| Deadcode gate for Python/Bun | Required |
| Deadcode coverage through linters for Go/Rust | Required |
| Complexity gate | Required |
| Acceptance gate | Strongly recommended |
| Architecture gate | Strongly recommended |
| CRAP advisory gate in `ci` | Required |
| Property-based tests under normal `test` | Strongly recommended |
| Mutation command | Contextual |
| `arch-config-guard` | Required |
| Layer 2 behavior contract text | Required for complete adoption; opt-in before wiring in brownfield |

## Command surface

The repo must expose these commands through its chosen runner:

| Command | Required behavior |
|---|---|
| `check` | Developer loop: fix, format, typecheck, test, suppression ratchet, and docs drift checks. May mutate. |
| `pre-commit` | Git pre-commit hook entrypoint. Same spirit as `check`, scoped to staged files where practical. May mutate. |
| `pre-push` | Offline read-only push gate: lint, format check where separate, acceptance, arch over the whole pushed tree. Must not mutate. |
| `ci` | Full read-only verification. Read-only gates run in parallel and print in submission order; then coverage and advisory CRAP run. Must not mutate tracked files. |
| `audit` | Dependency vulnerability audit. Must not mutate. |
| `post-edit` | Stop hook helper. Formats source files when changed. May mutate formatting only. |
| `stop-hook` | Agent Stop hook entrypoint. Runs `post-edit`, then read-only complexity and deadcode where applicable. |
| `setup-hooks` | Installs or refreshes pre-commit, pre-push, and Claude/Codex Stop hook wiring. |
| `suppressions` | Shows suppression counts; `--update-baseline` is the only writer and needs human sign-off. |
| `coverage` | Enforces the `.harness-baseline` `coverage.min` floor unless overridden. |
| `complexity` | Enforces lizard thresholds: CCN<=15, args<=8, length<=100. |
| `crap` | Advisory by default; `--enforce` hard-fails. Runs in `ci`, not `stop-hook`. |
| `acceptance` | Runs Gherkin acceptance tests when present. |
| `arch` | Runs the architecture boundary check when configured. |
| `arch-config-guard` | Warns or blocks protected arch config changes; strict mode allows `HARNESS_ALLOW_ARCH_CONFIG=1` after review. |
| `mutation` | Available as an explicit command; advisory and not wired into `ci`. |
| `agents-md-drift` | Fails if `AGENTS.md` differs from `CLAUDE.md`. |
| `sync-agents-md` | Writes `AGENTS.md <- CLAUDE.md`. |
| `deadcode` | Python/Bun only as a standalone command; Go/Rust cover dead code through lint gates. |

If a command is missing, recommend adding the smallest runner target that
provides the semantics. In brownfield repos, prefer delegating to existing
tooling over copying the template runner.

## Expected stages by command

Use the language reference to confirm exact tool names. The stages below are
the semantic contract.

### `check`

Must:

- Run fix/format before read-only checks.
- Run lint or equivalent quality checks.
- Run typecheck or compiler checks.
- Run tests. If the language template supports no-test fallback, preserve that
  fallback behavior.
- Check suppression growth against `.harness-baseline`.
- Check `AGENTS.md`/`CLAUDE.md` drift.
- Warn on missing Stop hook wiring where the template supports it.
- Warn on protected arch config changes through `arch-config-guard`.
- Print a short success summary, not raw command output.

Gap examples:

- Only runs tests: add fix, format, typecheck, suppression, and drift stages.
- Runs `pytest -vv` or full test logs on success: capture output and print a
  short pass line.
- Updates `.harness-baseline` automatically: remove automatic baseline writes;
  only `suppressions --update-baseline` may write it.

### `pre-commit`

Must:

- Be installed as `.git/hooks/pre-commit` or through the repo's configured
  git hooks path.
- Run on staged source files where practical.
- Fix/format, typecheck, test when source changed, and check suppressions and
  AGENTS/CLAUDE drift.
- Avoid network-dependent work.

Gap examples:

- Hook file missing: add `setup-hooks` support and install it.
- Runs all CI gates: split heavyweight read-only gates into `pre-push`/`ci`.
- Does not run when files are staged through a custom hooks path: resolve hook
  paths with `git rev-parse --git-path hooks/pre-commit`.

### `pre-push`

Must:

- Be installed as `.git/hooks/pre-push` or through the repo's configured git
  hooks path.
- Be read-only.
- Run offline deterministic gates over the whole pushed tree:
  lint, format check where separate, acceptance, arch.
- Run `arch-config-guard` in strict mode.
- Not run audit, coverage, CRAP, mutation, or network-bound checks.

Gap examples:

- Missing command or hook: add both.
- Runs formatters with write mode: change to format check.
- Duplicates `ci` exactly: remove network/advisory gates from `pre-push`.

### `ci`

Must:

- Be read-only and leave `git status --short` unchanged.
- Run read-only gates in parallel, capture output, run to completion, and print
  results in submission order.
- Include lint, typecheck where separate, audit, complexity, acceptance, arch,
  and deadcode for Python/Bun.
- Run `arch-config-guard` in strict mode.
- Then run tests/coverage and advisory CRAP.
- Check suppressions against `.harness-baseline`.

Language notes:

- Python: parallel batch includes lint, format check, typecheck, audit,
  complexity, deadcode, acceptance, arch; then coverage and advisory CRAP.
- Bun: parallel batch includes lint, typecheck, audit, complexity, deadcode,
  acceptance, arch; then coverage and advisory CRAP.
- Go: parallel batch includes lint, audit, complexity, acceptance, arch; then
  coverage and advisory CRAP. Dead code is covered by `golangci-lint unused`.
- Rust: parallel batch includes clippy, format check, complexity, acceptance,
  arch; then audit, tests/coverage, and advisory CRAP. Dead code is covered by
  strict clippy/rustc warnings.

Gap examples:

- Runs gates sequentially and stops at first failure: add a parallel read-only
  batch that reports all failures.
- Prints entire successful test suite output: capture success output and emit a
  short summary line.
- Runs format/fix: move mutation to `check`; keep `ci` read-only.
- Does not run CRAP: add advisory `crap` after coverage.

### `post-edit`

Must:

- Format source files changed by the agent.
- Avoid typecheck, tests, network, audit, coverage, CRAP, mutation, acceptance,
  or arch.
- Be safe to run from an agent Stop hook.

Gap examples:

- Runs the full test suite: move tests to `check`/`ci`; keep `post-edit`
  focused on formatting.
- Prints formatter output on success: capture it and print one short pass line.

### `stop-hook`

Must:

- Run `post-edit` first.
- Run `arch-config-guard` in warning mode.
- Then run read-only complexity and deadcode gates where applicable.
- Use Python/Bun deadcode gates (`vulture`/`knip`).
- Use lint-based deadcode coverage for Go/Rust; do not invent a standalone
  `deadcode` command there.
- Be wired into Claude and Codex Stop hooks.

Gap examples:

- Stop hook points directly at `check`: replace it with `stop-hook`.
- Codex hook points directly at a human runner: wrap it with
  `.codex/hooks/codex-stop-hook.sh` so stdout is JSON.
- Stop hook omits deadcode in Python/Bun: add the language deadcode gate.

## Supporting gates

### Suppressions

Must:

- Count suppressions for the language (`# noqa`, `// @ts-ignore`,
  `//nolint`, `#[allow]`, etc.).
- Fail `check`/`ci` when counts grow above `.harness-baseline`.
- Keep `coverage.min` in `.harness-baseline`.
- Let only `suppressions --update-baseline` write the baseline.

Fix suggestions:

- Add `.harness-baseline` with current counts and coverage floor after human
  sign-off.
- Add the suppression check to `check`, `pre-commit`, and `ci`.

### Coverage

Must:

- Run under `coverage`.
- Enforce the baseline `coverage.min` by default.
- Allow explicit override such as `--min=N` where the runner supports it.
- Produce reusable coverage data for CRAP where the language implementation
  expects it.

Fix suggestions:

- Move raw coverage report output behind failure or verbose mode.
- Generate the coverage artifact before `crap` in `ci`.

### CRAP

Must:

- Calculate complexity x coverage.
- Run in `ci` after coverage.
- Warn by default and hard-fail only with `--enforce`.
- Print offender details only when over threshold or when the command is
  explicitly reporting advisory findings.

Fix suggestions:

- Add `crap` as a standalone command and call it from `ci`.
- Do not run CRAP in `stop-hook`.
- Do not fail default `ci` solely because advisory CRAP offenders exist unless
  the repo intentionally enables enforcement.

### Complexity

Must:

- Use lizard pinned through `uvx` where the templates do.
- Enforce CCN<=15, args<=8, length<=100.
- Run in `ci` and `stop-hook`.

Fix suggestions:

- Add the `complexity` command and call it from both `ci` and `stop-hook`.
- Capture successful lizard output; print details only on failure.

### Deadcode

Must:

- Python: use vulture over app sources only, confidence 60, allowlist dynamic
  references in `vulture_allowlist.py`.
- Bun: use knip via `bunx`, configured by `knip.json`.
- Go: no standalone `deadcode`; verify `golangci-lint` includes `unused`.
- Rust: no standalone `deadcode`; verify strict clippy/rust warnings deny dead
  code.

Fix suggestions:

- Add a standalone `deadcode` command only for Python/Bun.
- Add Python/Bun deadcode to `ci` and `stop-hook`.
- For Go/Rust, fix the lint config instead of adding another tool.

### Property-based tests

Must:

- Run inside the normal `test` command; no separate required runner target.
- Use the template's language library:
  hypothesis, fast-check, rapid, or proptest.
- Cover law-like behavior with generated cases; examples alone are not enough.

Fix suggestions:

- Add the language PBT dev dependency when porting the contract or on the first
  law-like change.
- Seed properties around parsers, scoring functions, normalization, routing,
  or other invariant-heavy code.

### Acceptance

Must:

- Run Gherkin acceptance tests when present.
- Be included in `ci` and `pre-push`.
- Preserve the behavior contract's Gherkin-first workflow when Layer 2 is
  adopted.

Fix suggestions:

- Add an `acceptance` command even if it warns/skips when no feature files
  exist.
- Do not hide failing acceptance output; show command and captured output.

### Architecture

Must:

- Run the repo's architecture boundary check when configured.
- Be included in `ci` and `pre-push`.
- Keep the architecture config under `arch-config-guard`.
- Warn in `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.

Fix suggestions:

- Add `arch` as a standalone command.
- Add `arch-config-guard` as a standalone command and wire it into the runner
  stages.
- If no architecture tool exists, mark as contextual and recommend one only
  when module boundaries matter.

## Output contract

The runner must be quiet by default:

- Success prints one short line per step, such as `PASS Tests (12 passed)`.
- Raw stdout/stderr is captured and hidden on success.
- Failure prints the failing command and full captured output.
- `--verbose` streams raw command output.
- Parallel gate output is printed in submission order, not completion order.
- Advisory commands must be visible enough to act on, but must not drown out
  the primary pass/fail signal.

Audit commands:

```bash
<runner> check
<runner> ci
<runner> pre-push
<runner> stop-hook
<runner> --verbose check
```

Fail the output audit when:

- A successful test run dumps every test case or full suite logs.
- `ci` prints interleaved parallel output.
- A passing command hides all stage names.
- A failing command omits the command that failed.
- Codex Stop hook writes human status lines to stdout instead of JSON.

Fix suggestions:

- Wrap subprocess calls with a shared `run()` helper.
- Capture stdout/stderr by default.
- Add an explicit `--verbose` path for raw streaming output.
- For Codex Stop, route human output to stderr and emit exactly one JSON
  object on stdout.

## Hook wiring

Must verify:

- `setup-hooks` installs or refreshes both `pre-commit` and `pre-push`.
- Git hook paths respect `git rev-parse --git-path`.
- Claude Stop hook runs `<runner> stop-hook`.
- Codex Stop hook runs `.codex/hooks/codex-stop-hook.sh <runner> stop-hook`.
- Codex Stop hook has timeout and status message.
- Claude settings wire Stop only.
- No `.claude/scripts/` behavior hooks are required.

Fix suggestions:

- Preserve existing `.claude/settings.json` keys while merging harness hooks.
- Preserve existing `.codex/hooks.json` keys while adding Stop hook wiring.
- Do not point Codex directly at the runner.

## Docs and behavior contract

Must verify:

- `AGENTS.md` and `CLAUDE.md` both exist.
- Both files hold the full command and behavior contract content, not stubs.
- `agents-md-drift` fails on drift.
- `sync-agents-md` writes `AGENTS.md <- CLAUDE.md`.
- Layer 2 contract text appears in both files when complete adoption is the
  target.
- Commit/push ownership and Gherkin-first rules are present as instructions.
- Arch config review is documented as an `arch-config-guard` integration gate.

Fix suggestions:

- Merge existing instructions; do not overwrite user-specific repo guidance.
- If only one file exists, create the other with the same full content.
- Add `agents-md-drift` to `check` and `pre-commit`.

## Greenfield checklist

Even greenfield adoption must be verified:

1. Copy the matching template.
2. Install dependencies.
3. Run `setup-hooks`.
4. Run `check`.
5. Run `ci`; confirm `git status --short` is unchanged.
6. Run `pre-push`.
7. Run `audit`.
8. Run `stop-hook`.
9. Confirm pre-commit and pre-push hooks exist.
10. Confirm Claude and Codex Stop hooks point at `stop-hook`.
11. Confirm `AGENTS.md` and `CLAUDE.md` are byte-identical.
12. Confirm `arch-config-guard --warn` runs.
13. Confirm successful commands print short stage summaries.
14. Confirm `--verbose` shows raw command output.

## Brownfield audit procedure

1. Identify language and runner.
2. List current harness commands and aliases.
3. For each required command, record present/missing.
4. For each present command, inspect its substages.
5. Run the command when safe; capture whether it mutates tracked files.
6. Compare success output against the quiet output contract.
7. Inspect git hook installation and hook path handling.
8. Inspect Claude/Codex hook JSON.
9. Inspect AGENTS.md/CLAUDE.md content and drift tooling.
10. Inspect `.harness-baseline` and suppression/coverage behavior.
11. Inspect PBT, acceptance, arch, CRAP, complexity, and deadcode coverage.
12. Produce the report below with actionable fixes.

Do not edit during the audit unless the user asked for fixes. If asked to fix,
make the smallest change that brings the missing semantic behavior in line with
the contract.

## Adoption report format

Use this structure:

```markdown
# Harness Adoption Audit

Repo mode: greenfield | brownfield | monorepo
Runner: <runner>
Language reference: <reference file>
Target: complete harness adoption

## Summary

- Required gaps: <count>
- Strongly recommended gaps: <count>
- Contextual gaps: <count>
- Skipped by decision: <count>

## Findings

| Severity | Area | Status | Evidence | Fix |
|---|---|---|---|---|
| Required | ci output | Fails | `ci` prints full test logs on success | Capture output in runner `run()` and print a short pass line; keep full output for failure or `--verbose`. |

## Command Matrix

| Command | Present | Stages correct | Output correct | Mutates correctly | Fix |
|---|---:|---:|---:|---:|---|
| check | yes/no | yes/no | yes/no | yes/no | <action> |
| pre-commit | yes/no | yes/no | yes/no | yes/no | <action> |
| pre-push | yes/no | yes/no | yes/no | yes/no | <action> |
| ci | yes/no | yes/no | yes/no | yes/no | <action> |
| audit | yes/no | yes/no | yes/no | yes/no | <action> |
| post-edit | yes/no | yes/no | yes/no | yes/no | <action> |
| stop-hook | yes/no | yes/no | yes/no | yes/no | <action> |

## Supporting Gates

| Gate | Status | Evidence | Fix |
|---|---|---|---|
| suppressions | pass/fail/missing/skipped | <evidence> | <action> |
| coverage | pass/fail/missing/skipped | <evidence> | <action> |
| complexity | pass/fail/missing/skipped | <evidence> | <action> |
| deadcode | pass/fail/missing/skipped | <evidence> | <action> |
| CRAP | pass/fail/missing/skipped | <evidence> | <action> |
| PBT | pass/fail/missing/skipped | <evidence> | <action> |
| acceptance | pass/fail/missing/skipped | <evidence> | <action> |
| arch | pass/fail/missing/skipped | <evidence> | <action> |
| arch-config-guard | pass/fail/missing/skipped | <evidence> | <action> |

## Hooks And Docs

| Item | Status | Evidence | Fix |
|---|---|---|---|
| pre-commit hook | pass/fail/missing | <evidence> | <action> |
| pre-push hook | pass/fail/missing | <evidence> | <action> |
| Claude Stop hook | pass/fail/missing | <evidence> | <action> |
| Codex Stop hook | pass/fail/missing | <evidence> | <action> |
| Layer 2 contract text | pass/fail/missing/skipped | <evidence> | <action> |
| AGENTS/CLAUDE parity | pass/fail/missing | <evidence> | <action> |

## Suggested Fix Order

1. <smallest required fix that unlocks verification>
2. <next required fix>
3. <strongly recommended fix>
```

Each finding must include evidence and an action. Avoid vague fixes like
"improve CI"; write the missing command, stage, hook, or output behavior.
