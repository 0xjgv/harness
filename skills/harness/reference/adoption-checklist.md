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

## Progressive adoption

**The harness gates the change, not the codebase.** Every gate is on in a
brownfield repo from day one; none of them are aimed at pre-existing code.
Two mechanisms, split by whether a finding has a natural count:

| Finding kind | Mechanism | Commands | Scope |
|---|---|---|---|
| No natural count (lint diagnostic, format diff, type error, which tests to run) | **diff-scoped** — git-derived file list, skip with a warning when empty, never widen to whole tree | `fix`, `format`, `lint`, `typecheck`, `test` (in `check`/`pre-commit`) | changed set; `--all` overrides, `--base=<ref>` picks the base |
| Natural count (coverage %, mutants killed, functions over CCN, duplicate blocks, CRAP offenders, arch violations, deadcode findings, suppressions) | **baseline floor starting where the repo already is** | `coverage`, `mutation`, `complexity`, `crap`, `arch`, `deadcode`, `suppressions` | whole tree — scoping a count makes it meaningless (mutation excepted: scoped, because a run costs minutes) |

The change set depends on the stage: local stages (`check`, `pre-commit`,
`post-edit`) use the uncommitted set (`git status --porcelain`); anything with
a resolved base ref (`--base=<ref>`, `HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`,
then the `origin/HEAD` → `origin/main` → `main` fallbacks) uses
`git diff --name-only <base>...HEAD`. `ci`, `pre-push`, and `--all` run the
whole tree. The `git status` helper must never feed `ci`: there it yields an
empty set and a green gate that tested nothing.

Audit consequences:

- First run is the same three commands for greenfield and brownfield:
  `suppressions --update-baseline`, `check`, `setup-hooks`. It must be green
  in both. If a brownfield `check` is red on pre-existing code, the finding
  is a **Required** gap in the harness, not a backlog item for the repo.
- A scoped gate that falls back to the whole tree on an empty scope is a
  **Required** defect. Report it; do not treat it as a stricter-is-better
  variation.
- Progressive adoption is never "skipped by decision" — nothing was skipped.
- Diff-scoping is a trade-off to state, not hide: a green scoped run is not a
  clean repo. Point the user at `/ratchet` (`skills/ratchet/`) as the crank
  that moves the floors, by adding tests only.
- Do not recommend widening the scoped gates, raising a floor above measured,
  or hand-fixing pre-existing violations as part of adoption.

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
| Diff-scoped `fix`/`format`/`lint`/`typecheck`/`test`, skipping on empty scope | Required |
| `--all` and `--base=<ref>` scope overrides | Required |
| Seven baseline floor families in `.harness-baseline` (`coverage.min`, `mutation.min`, `complexity.max_violations`, `duplication.max_blocks`, `crap.max_violations`, `arch.max_violations`, `deadcode.max_findings`, `suppressions.*`); a missing key is report-only | Required |
| `suppressions --update-baseline` as the sole writer of all of them (`mutation.min` only under `--with-mutation`) | Required |
| AGENTS.md/CLAUDE.md full-content parity | Required |
| `agents-md-drift` and `sync-agents-md` | Required |
| Deadcode gate for Python/Bun | Required |
| Deadcode coverage through linters for Go/Rust | Required |
| Complexity gate | Required |
| Acceptance gate | Strongly recommended |
| Architecture gate | Strongly recommended |
| CRAP advisory gate in `ci` | Required |
| Property-based tests under normal `test` | Strongly recommended |
| `mutation` command, scoped, advisory in `ci` after coverage | Strongly recommended |
| `arch-config-guard` | Required |
| `gherkin-guard` (mechanical Gherkin-first enforcement) | Required |
| Stop hook exits 2 with a stderr failure summary on failure | Required |
| Layer 2 behavior contract text | Required for complete adoption; opt-in before wiring in brownfield |

## Command surface

The repo must expose these commands through its chosen runner:

| Command | Required behavior |
|---|---|
| `check` | Developer loop: fix, format, typecheck, test, plus every other gate that is offline, fast, and takes no build lock (complexity, acceptance, deadcode where shipped, a lockfile/module-tidy check where shipped); warns (does not block) via `arch-config-guard` and `gherkin-guard`; suppression ratchet and docs drift checks. May mutate. |
| `pre-commit` | Git pre-commit hook entrypoint. Same spirit as `check`, scoped to staged files where practical, then re-stages (`git add`) the files it fixed. May mutate. |
| `pre-push` | Offline read-only push gate: lint, format check where separate, acceptance, arch over the whole pushed tree; strict `arch-config-guard` + `gherkin-guard`. Must not mutate. |
| `ci` | Full read-only verification. Read-only gates run in parallel and print in submission order; then coverage and advisory CRAP run; strict `arch-config-guard` + `gherkin-guard`. Must not mutate tracked files. |
| `audit` | Dependency vulnerability audit. Must not mutate. |
| `post-edit` | Stop hook helper. Formats source files when changed, using repo-root-relative paths so it also works from a subdirectory. May mutate formatting only. |
| `stop-hook` | Agent Stop hook entrypoint. Runs `post-edit`, then read-only complexity and deadcode where applicable. **On failure, exits 2 and writes a short failure summary to stderr** — Claude Code only treats exit code 2 as blocking and only reads the failure back from stderr; any other exit code is silently swallowed and the agent never sees it. Every Claude Stop-hook command ends in `|| exit 2` to re-assert this. |
| `setup-hooks` | Installs or refreshes pre-commit, pre-push, and Claude/Codex Stop hook wiring. |
| `suppressions` | Shows suppression counts; `--update-baseline` is the only writer and needs human sign-off. |
| `coverage` | Enforces the `.harness-baseline` `coverage.min` floor unless overridden. |
| `complexity` | Enforces lizard thresholds: CCN<=15, args<=8, length<=100, against `complexity.max_violations`; a second lizard run with `-Eduplicate` counts duplicate blocks against `duplication.max_blocks`. Both report-only when the key is missing. |
| `crap` | Advisory by default (prints `⚠`, not `✗`); `--enforce` hard-fails. Runs in `ci`, not `stop-hook`. |
| `acceptance` | Runs Gherkin acceptance tests when present. |
| `arch` | Runs the architecture boundary check when configured and counts its violations against `arch.max_violations` (report-only when the key is missing; warns and skips when no arch config exists). Also runs in `check` when the tool is offline and takes no build lock (python/bun); stays `ci`/`pre-push`-only when it needs the network or a build lock (go/rust). |
| `arch-config-guard` | Warns or blocks protected arch config changes; strict mode allows `HARNESS_ALLOW_ARCH_CONFIG=1` after review. |
| `gherkin-guard` | Warns (in `check`/`stop-hook`) or blocks (in `pre-commit`/`pre-push`/`ci`) a changed production-source file with no accompanying `.feature` change; strict mode allows `HARNESS_ALLOW_NO_FEATURE=1` after review. Skips silently when the repo has no `.feature` files anywhere. |
| `mutation` | Scoped to the changed source files (`--all` for the whole tree); prints the integer % of mutants killed and compares it to `mutation.min`. Advisory: prints `⚠` on a miss; only `--enforce` hard-fails. Runs in `ci` after coverage, advisory there too. Warns and skips on an empty scope or a missing tool. |
| `agents-md-drift` | Fails if `AGENTS.md` differs from `CLAUDE.md`. |
| `sync-agents-md` | Writes `AGENTS.md <- CLAUDE.md`. |
| `deadcode` | Python/Bun only as a standalone command; Go/Rust cover dead code through lint gates. Runs in `check` + `ci` + `stop-hook` where present. |

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
- Run tests, scoped: the changed test files plus the tests that map to the
  changed source modules (mapping per language reference). An empty scope
  warns and skips; a changed source file with no mapped test gets one `⚠`
  line per file, never a failure, and the mapped tests still run; `--all`
  runs the whole suite. If the language template supports no-test fallback,
  preserve that fallback behavior.
- Run every other gate that is offline, fast, and takes no build lock:
  complexity, acceptance (self-skip with a warning if no `.feature` files
  exist), the deadcode gate where the language ships one, and a
  lockfile/module-tidy check where the language ships one (`uv lock --check`,
  `bun install --frozen-lockfile`, `go mod tidy -diff`). Whether the
  architecture boundary check itself (`arch`) belongs in `check` is a
  per-language call: python/bun's tools (import-linter, dependency-cruiser)
  are offline and take no build lock, so `check` runs them; go/rust's tools
  fetch a module or take cargo's exclusive build lock, so `arch` stays
  `ci`/`pre-push`-only there. Do not move `arch` into `check` without
  confirming it is actually offline and lock-free for that language/tool.
- Check suppression growth against `.harness-baseline`.
- Check `AGENTS.md`/`CLAUDE.md` drift.
- Warn (do not block) on missing Stop hook wiring where the template supports it.
- Warn (do not block) on protected arch config changes through `arch-config-guard`.
- Warn (do not block) on a changed production-source file with no matching
  `.feature` change, through `gherkin-guard`.
- Print a short success summary, not raw command output.

Gap examples:

- Only runs tests: add fix, format, typecheck, suppression, and drift stages.
- Runs `pytest -vv` or full test logs on success: capture output and print a
  short pass line.
- Updates `.harness-baseline` automatically: remove automatic baseline writes;
  only `suppressions --update-baseline` may write it.
- `check` and `ci` silently diverge on which offline gates each runs: keep the
  invariant `ci` minus `check` == {network audit, coverage, advisory CRAP,
  plus `arch` only where it needs the network or a build lock} explicit and
  true.

### `pre-commit`

Must:

- Be installed as `.git/hooks/pre-commit` or through the repo's configured
  git hooks path.
- Run on staged source files where practical.
- Fix/format, typecheck, the scoped test run (same mapping rule as `check`,
  over the staged set), and check suppressions and AGENTS/CLAUDE drift.
- Re-stage (`git add`) the files it fixed, so the commit records the fixed
  content. Note the trade-off: for a partially staged file, this also stages
  its remaining unstaged hunks — the same trade-off `lint-staged` makes.
- Avoid network-dependent work.

Gap examples:

- Hook file missing: add `setup-hooks` support and install it.
- Runs all CI gates: split heavyweight read-only gates into `pre-push`/`ci`.
- Does not run when files are staged through a custom hooks path: resolve hook
  paths with `git rev-parse --git-path hooks/pre-commit`.
- Fixes files but never re-stages them: the commit ships the pre-fix content
  even though `pre-commit` reported success.

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
- Then run the whole test suite under coverage, advisory CRAP, and advisory
  `mutation` over the base-ref diff (`git diff --name-only <base>...HEAD`,
  never `git status`). A mutation miss prints `⚠` and never fails `ci`.
- Check every `.harness-baseline` floor.

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
- Skips mutation, or fails `ci` on a mutation miss: add advisory `mutation`
  after coverage; only the standalone command with `--enforce` may fail.

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
- Run `arch-config-guard` and `gherkin-guard` in warning mode.
- Then run read-only complexity and deadcode gates where applicable.
- Use Python/Bun deadcode gates (`vulture`/`knip`).
- Use lint-based deadcode coverage for Go/Rust; do not invent a standalone
  `deadcode` command there.
- **On failure, exit 2 and write a short failure summary to stderr.** This is
  the single most important behavior in the whole contract: Claude Code only
  treats a Stop hook's exit code **2** as blocking, feeding stderr back to the
  model so the agent must address the failure before stopping. Any other
  non-zero exit is silently swallowed — the agent never sees it, and a
  failing complexity/deadcode gate stops the turn with no feedback loop at
  all. If the runner's own process can't reliably propagate exit code 2
  through its invocation wrapper (measured case: `go run` collapses a failing
  child's exit code to 1 and prints `exit status N` to stderr), the Claude
  Stop-hook command itself must end in `|| exit 2` to re-assert it at the
  shell level. `check`/`ci`/`pre-commit`/`pre-push` keep exiting 1 on
  failure — only `stop-hook`'s Claude wiring needs exit 2.
- Be wired into Claude and Codex Stop hooks.

Gap examples:

- Stop hook points directly at `check`: replace it with `stop-hook`.
- Codex hook points directly at a human runner: wrap it with
  `.codex/hooks/codex-stop-hook.sh` so stdout is JSON.
- Stop hook omits deadcode in Python/Bun: add the language deadcode gate.
- Stop hook exits 1 (or any code but 2) on failure, or the Claude hook command
  has no `|| exit 2` suffix: Claude Code silently swallows the failure and the
  agent stops without ever seeing it. Fix both the runner's own exit code and
  the hook command suffix.

## Supporting gates

### Baseline floors (`.harness-baseline`)

The baseline is **not** suppression-only. It records seven metric families
(eight keys), each a floor that starts where the repo already is. The key
names are identical in all four languages:

```
arch.max_violations 0
complexity.max_violations 0
coverage.min 100
crap.max_violations 0
deadcode.max_findings 0
duplication.max_blocks 0
mutation.min 78          # only present after --with-mutation
suppressions.noqa 8
suppressions.pyright_ignore 2
suppressions.type_ignore 4
```

| Key | Direction | Measures |
|---|---|---|
| `complexity.max_violations` | down | lizard warnings over CCN 15 / args 8 / length 100 |
| `duplication.max_blocks` | down | lizard `-Eduplicate` duplicate blocks |
| `crap.max_violations` | down | functions over the CRAP threshold |
| `arch.max_violations` | down | boundary violations reported by the arch tool |
| `mutation.min` | up | integer % of mutants killed on the last scoped run |
| `coverage.min` | up | coverage percent |
| `deadcode.max_findings` | down | python/bun deadcode findings |
| `suppressions.<kind>` | down | per-kind suppression counts (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`) |

Must:

- Count suppressions for the language (`# noqa`, `// @ts-ignore`,
  `//nolint`, `#[allow]`, etc.).
- Fail `check`/`ci` when any floor is crossed — except `mutation.min`, which
  is advisory everywhere but the standalone command with `--enforce`.
- Treat a **missing key** as report-only for that one gate: it passes with a
  `report-only: no .harness-baseline floor` label and a hint to run the
  update. A missing file is the same for every gate. A missing baseline must
  never fail a gate.
- **Re-measure** every key on update, so a metric that improved ratchets
  down; preserve unrecognised keys untouched. `mutation.min` is the one
  exception: the automatic pass carries its existing value through untouched
  and measures it only under `--with-mutation` (a mutation run costs
  minutes).
- Make the update **all-or-nothing**: a metric that errors aborts the write
  and is named; a metric that cannot be measured (no arch config, no tests,
  no deadcode tool) has its key *dropped* with a warning (absence on disk
  means report-only, which is what stops the template's own
  `coverage.min 100` from being inherited). Suppression kinds ratcheted to
  zero are recorded as `0`, not dropped.
- Score `mutation.min` as `round(100 * killed / (killed + survived))`,
  killed = caught + timeout, survived = ran and not detected; unviable,
  compile-error, skipped, and not-covered mutants count in neither term;
  `killed + survived == 0` is unavailable, i.e. report-only. Never use the
  mutation tool's own threshold flag — denominators differ per tool.
- Let only `suppressions --update-baseline` write the baseline. Confirm no
  other code path writes it — an automatic write destroys the ratchet.

Fix suggestions:

- Add `.harness-baseline` by running `suppressions --update-baseline` after
  human sign-off — never by hand-writing target numbers.
- Add the baseline check to `check`, `pre-commit`, and `ci`.
- Record the naming wart in the report: `suppressions --update-baseline`
  writes all seven families. The name is misleading and deliberately unfixed
  (`suppressions` is in the parity gate's non-allowlistable core command
  list, so a `baseline` command would have to land in all four templates).
  An adopter will not guess it — name it explicitly in the handover.

### Coverage

Must:

- Run under `coverage`.
- Enforce the baseline `coverage.min` floor by default — the number
  `suppressions --update-baseline` measured on this repo, not a target.
- Pass the floor explicitly on the tool's CLI (`--fail-under=<n>`), so a
  config-file value such as `[tool.coverage.report] fail_under` cannot
  silently win.
- Allow explicit override such as `--min=N` where the runner supports it.
- Note the greenfield/brownfield asymmetry: the committed template baseline
  ships `coverage.min 100`, which greenfield inherits and brownfield never
  sees, because first run re-baselines against the adopter's own repo. An
  adopter who spots `100` in a freshly copied `.harness-baseline` must be
  told it is about to be overwritten, not a bar they have to clear.
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

### Mutation

Must:

- Scope to the changed source files: the uncommitted set locally, the
  base-ref diff in `ci`; `--all` widens to the whole tree. Warn and skip on
  an empty scope or when the tool is not installed.
- Print the integer % killed and compare it to `mutation.min`; advisory by
  default (`⚠` on a miss), hard-fail only with `--enforce`.
- Run in `ci` after coverage, advisory. Not in `check`, `pre-push`, or
  `stop-hook`.
- Be written to `.harness-baseline` only by
  `suppressions --update-baseline --with-mutation`.

Fix suggestions:

- Add `mutation` as a standalone command and call it from `ci` after coverage.
- Compute the score in the runner from the tool's report; do not pass the
  tool a threshold flag.
- Gitignore the tool's output directory and wipe it in `clean`.

### Complexity

Must:

- Use lizard, pinned. Python pins it as a dev dependency and resolves it
  through the runner's tool resolver (`uv run` → `.venv/bin/` → `uvx`), so
  `uvx` on PATH is a fallback rather than a hard requirement.
- Enforce CCN<=15, args<=8, length<=100.
- Run in `check`, `ci`, and `stop-hook`.
- Stay whole-tree, and gate on `complexity.max_violations` from
  `.harness-baseline` rather than on zero.
- Run lizard a second time with `-Eduplicate` over the same target set and
  gate the `Duplicate block:` count on `duplication.max_blocks`. Lizard's
  exit code ignores duplicates, so the runner compares the count itself.

Fix suggestions:

- Add the `complexity` command and call it from both `ci` and `stop-hook`.
- Capture successful lizard output; print details only on failure.
- Missing duplication gate: add the `-Eduplicate` invocation with the same
  targets as the CCN run; a different target set makes the floor
  irreproducible.

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
- Count the violations it reports and gate on `arch.max_violations` from
  `.harness-baseline`; report-only when the key is missing; warn and skip
  when no arch config exists.
- Be included in `ci` and `pre-push`.
- Keep the architecture config under `arch-config-guard`.
- Warn in `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
- In a brownfield repo, **derive the contract from the real package tree and
  baseline the violations — never delete or loosen the arch config to get a
  green first run.** Deleting the config is a "Required" defect in the
  adoption, not a shortcut.

Fix suggestions:

- Add `arch` as a standalone command.
- Add `arch-config-guard` as a standalone command and wire it into the runner
  stages.
- Arch config was deleted or gutted during adoption: restore it, write the
  layers the package tree actually has, run
  `suppressions --update-baseline`, and let `arch.max_violations` hold the
  count until `/ratchet` or a human moves it.
- If no architecture tool exists, mark as contextual and recommend one only
  when module boundaries matter.

### Gherkin-first guard

Must:

- Expose a `gherkin-guard` command that triggers when a changed
  production-source file has no accompanying changed `.feature` file.
- Be included in `check`/`stop-hook` as a warning (never blocks) and in
  `pre-commit`/`pre-push`/`ci` as a blocker, allowing
  `HARNESS_ALLOW_NO_FEATURE=1` as a reviewed override — the same shape as
  `arch-config-guard`.
- Skip entirely and silently when the repo has no `.feature` files anywhere
  yet. This is deliberate: retrofitting the harness into a repo with no
  acceptance suite must never block on this gate.
- Exclude the harness runner file itself from "production source" — the
  harness is tooling, not product behavior.

Fix suggestions:

- Add `gherkin-guard` as a standalone command and wire it into the same
  stages as `arch-config-guard`.
- If the repo has no acceptance suite yet, confirm the guard self-skips
  rather than hand-rolling an exemption.
- Do not block on `gherkin-guard` in `check`/`stop-hook` — those stages warn
  only, matching `arch-config-guard`'s split between fast local feedback and
  strict integration gates.

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
- Commit/push ownership and task-sizing rules are present as instructions.
- Gherkin-first is present as instructions **and** mechanically enforced via
  `gherkin-guard`.
- Arch config review is present as instructions **and** mechanically enforced
  via `arch-config-guard`.

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
10. Confirm Claude and Codex Stop hooks point at `stop-hook`, the Claude
    command ends in `|| exit 2`, and a forced failure actually blocks (exit 2,
    stderr summary) rather than being silently swallowed.
11. Confirm `AGENTS.md` and `CLAUDE.md` hold the same content — after
    `ls -la` on both. A tracked symlink or a lowercase `agents.md` twin makes
    "identical" mean something different than it looks. In a repo whose own
    `AGENTS.md` legitimately differs, `HARNESS_ALLOW_AGENTS_MD_DRIFT=1` is the
    documented retrofit path, not a defect.
12. Confirm `arch-config-guard --warn` runs.
13. Confirm `gherkin-guard --warn` runs and skips silently when the template
    has no `.feature` files.
14. Confirm successful commands print short stage summaries.
15. Confirm `--verbose` shows raw command output.

## Brownfield first run

Before auditing anything else, confirm these commands work in this order and
that step 2 is green:

1. `<runner> suppressions --update-baseline` — snapshot every metric as it is
   today. This is the only writer of `.harness-baseline`, and it is
   all-or-nothing: it aborts and names the metric that errored, and drops
   (with a warning) a key it cannot measure. `mutation.min` is not in the
   automatic set; add `--with-mutation` when the repo has a mutation tool
   and the minutes to spare, or leave it report-only for now.
2. `<runner> check` — must be green: the scoped gates see only the diff and
   every counted metric sits exactly at its just-written floor. Expect `⚠`
   lines from the scoped `test` gate for changed source files with no
   mapped test — those are the first `/ratchet` characterization targets,
   not failures.
3. **The human commits the adoption.** Not the agent — the behavior contract
   forbids it. Until the adoption diff is committed, every later `check`
   re-scopes over it, and "green" means green over the harness's own
   uncommitted changes. This is *not* "green by construction": the adoption
   modifies the runner file itself, which is inside the quality sources, so
   the first-run scope is never empty. Verify, do not assume.
4. `<runner> setup-hooks`, then **re-read the hook JSON**. `setup-hooks`
   regenerates the Stop-hook commands from constants inside the runner, so a
   hand-edited `.claude/settings.json` is silently reverted. Adapt the
   constants, not the JSON.
5. Run the Stop-hook command **verbatim** once. The `✓ Stop hook wiring` gate
   only greps the file for `Stop` and `stop-hook`; it goes green on a command
   that cannot execute (e.g. `uv run harness` in a repo with no console
   script).

Also confirm what the repo does **not** need: if it has no `pyproject.toml`,
or one with no `[project]` table, do not create or add one. That is a build
and packaging migration, not harness adoption. But do not conclude the repo
therefore goes without the ruleset — ruff, the type checker, and coverage all
have config files that are not `pyproject.toml` (`ruff.toml`,
`pyrightconfig.json`, `.coveragerc`); see the vehicle table in
[python.md](python.md). The Python runner resolves tools out of `.venv/bin/`
and must be invoked with an interpreter ≥3.10 — `python3` on macOS is 3.9 and
fails at import, so use `.venv/bin/python harness.py <target>`. Its lockfile
gate warns and skips with no `uv.lock`.

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
10. Inspect `.harness-baseline`: the seven metric families present under
    their fixed key names (`mutation.min` may be absent until
    `--with-mutation` has run), single writer, missing-file and missing-key
    cases report-only.
11. Inspect scoping: do `fix`/`format`/`lint`/`typecheck`/`test` resolve a
    git-derived file list, skip on an empty scope, and honour `--all` /
    `--base=<ref>`? Does `test` warn per changed source file with no mapped
    test and still run the mapped ones? A whole-tree fallback is a Required
    defect; so is a `git status` change set inside `ci`.
12. Inspect PBT, acceptance, arch (config present, violations baselined, not
    deleted), CRAP, complexity + duplication, mutation, and deadcode
    coverage.
13. Produce the report below with actionable fixes.

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
| diff scoping (fix/format/lint/typecheck/test) | pass/fail/missing | <evidence> | <action> |
| baseline floors (7 families, single writer) | pass/fail/missing/skipped | <evidence> | <action> |
| suppressions | pass/fail/missing/skipped | <evidence> | <action> |
| coverage | pass/fail/missing/skipped | <evidence> | <action> |
| mutation | pass/fail/missing/skipped | <evidence> | <action> |
| complexity + duplication | pass/fail/missing/skipped | <evidence> | <action> |
| deadcode | pass/fail/missing/skipped | <evidence> | <action> |
| CRAP | pass/fail/missing/skipped | <evidence> | <action> |
| PBT | pass/fail/missing/skipped | <evidence> | <action> |
| acceptance | pass/fail/missing/skipped | <evidence> | <action> |
| arch | pass/fail/missing/skipped | <evidence> | <action> |
| arch-config-guard | pass/fail/missing/skipped | <evidence> | <action> |
| gherkin-guard | pass/fail/missing/skipped | <evidence> | <action> |
| stop-hook exit code 2 | pass/fail/missing | <evidence> | <action> |

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
