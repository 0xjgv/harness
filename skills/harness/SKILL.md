---
name: harness
description: >
  Bootstrap or align a repo with the harness-templates contract: the
  seven-stage quality harness (check, pre-commit, pre-push, ci, audit,
  post-edit, stop-hook) plus the behavior contract (task sizing, human-owned
  commits, Gherkin-first, arch-config integration guard). Use when starting a
  new python/bun/go/rust/monorepo project, adding a quality harness to an
  existing repo, or asked to match ~/Code/harness-templates conventions.
  Triggers: "add a harness", "set up check/ci/pre-commit", "align with
  harness-templates", "bootstrap quality gates", "add the behavior
  contract", "wire the engineering principles".
---

# Harness Template

Give a repo the same contract as `~/Code/harness-templates`. The contract
has **two layers**:

- **Layer 1 — quality harness** (always): the 7 stages `check`,
  `pre-commit`, `pre-push`, `ci`, `audit`, `post-edit`, `stop-hook`.
- **Layer 2 — behavior contract** (greenfield: automatic; existing repo:
  opt-in): `AGENTS.md`/`CLAUDE.md` instructions plus two mechanically
  enforced guards, `arch-config-guard` and `gherkin-guard`, which warn
  during `check`/`stop-hook` and block `pre-commit`/`pre-push`/`ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` / `HARNESS_ALLOW_NO_FEATURE=1` is set after
  review. See [behavior-contract.md](reference/behavior-contract.md).

## Source of truth

Always read first. Do not restate the contract from memory.

- `~/Code/harness-templates/README.md` — Layer-1 contract + getting started
- `~/Code/harness-templates/<lang>/AGENTS.md` and `…/CLAUDE.md` — same
  per-language commands + behavior contract, byte-identical. Claude Code
  reads `CLAUDE.md`; Codex (and other AGENTS.md-consuming tools) read
  `AGENTS.md`. Both files carry the full content; the harness
  `agents-md-drift` check enforces no drift, and `sync-agents-md` writes
  `AGENTS.md ← CLAUDE.md` after edits. Copy verbatim; do not paraphrase.
- `~/Code/harness-templates/<lang>/.claude/settings.json` — Claude hooks
- `~/Code/harness-templates/<lang>/.codex/hooks.json` — Codex Stop hook
- `~/Code/harness-templates/<lang>/.codex/hooks/codex-stop-hook.sh` —
  Codex stdout-to-JSON wrapper for Stop hooks
- `~/Code/harness-templates/python/harness.py` `run()` — canonical quiet output

Never edit anything under `~/Code/harness-templates/`.

## Decide first

1. **Empty dir** → copy a template verbatim (see per-language reference).
   Layer 2 comes through `AGENTS.md`/`CLAUDE.md` and the runner's
   `arch-config-guard` — keep it.
2. **Existing repo with a runner** (just/make/npm scripts/cargo) → adapt
   Layer 1; reuse the existing runner. Do **not** add a second one.
   Layer 2 is **opt-in** — ask before wiring it because it adds explicit
   agent instructions and arch-config integration failures.
3. **Polyglot / multi-project** → use `monorepo/` Makefile dispatch.

When auditing or repairing a repo that already has some harness pieces,
read [adoption-checklist.md](reference/adoption-checklist.md)
before language-specific references. Use it to find missing commands,
incorrect command substages, noisy success output, and incomplete hook/docs
wiring, then apply the smallest compatible fix.

## Language detection

| Signal | Template |
|---|---|
| `pyproject.toml` / `uv.lock` | `python/` → [python.md](reference/python.md) |
| `package.json` + `bun.lock` | `bun/` → [bun.md](reference/bun.md) |
| `Cargo.toml` | `rust/` → [rust.md](reference/rust.md) |
| `go.mod` | `go/` → [go.md](reference/go.md) |
| `Makefile` + multiple subprojects | `monorepo/` → [monorepo.md](reference/monorepo.md) |

Claude/Codex hook + Stop-hook shape: [settings-json.md](reference/settings-json.md).
The Stop hook runs `stop-hook`; `stop-hook` runs `post-edit`, then the
read-only complexity gate (plus the dead-code gate where the language ships
one) in parallel. **`stop-hook` exits 2 and writes a failure summary to
stderr on failure — this is the only exit code Claude Code's Stop hook
treats as blocking, feeding stderr back to the model; every other exit code
is silently swallowed and the agent never sees the failure.** Every Claude
Stop-hook command ends in `|| exit 2` to re-assert this (`go run` otherwise
collapses a failing child's exit code to 1) — see settings-json.md above for
the exact hook JSON.
Behavior contract: [behavior-contract.md](reference/behavior-contract.md).

## Layer 1 — the seven-stage contract

| Stage | When | What | Fixes? |
|---|---|---|---|
| `check` | After edits | fix + format + typecheck + test (scoped to the tests that map to changed modules; `--all` for the whole suite), plus every other gate that is offline, fast, and takes no build lock (complexity + acceptance + deadcode where shipped + a lockfile check in python/bun + `go mod tidy -diff` in go + — python/bun only — the architecture boundary check `arch` itself); warns via `arch-config-guard` + `gherkin-guard`; checks agents-md drift; suppression ratchet | yes |
| `pre-commit` | Git pre-commit hook | same, staged files only, then re-stages (`git add`) the files it fixed | yes |
| `pre-push` | Before push | read-only push gate: lint + format check + acceptance + arch over the whole tree, in parallel; strict `arch-config-guard` + `gherkin-guard` | no |
| `ci` | CI pipeline | read-only gates (lint + typecheck + dep audit + complexity + deadcode + acceptance + arch) **run in parallel**, captured and printed in submission order; then the whole test suite under coverage + crap (advisory) + mutation (advisory, scoped to the base-ref diff); strict `arch-config-guard` + `gherkin-guard` | no |
| `audit` | CI pipeline | dependency vulnerability audit | no |
| `post-edit` | Stop hook helper | format if source files changed | yes |
| `stop-hook` | Agent Stop hook | post-edit + complexity + deadcode (python/bun); **exits 2 with a stderr failure summary on failure** | yes |

Invariant every runner documents: `ci` minus `check` is only the network
dependency audit, coverage (which runs the whole suite where `check` ran the
scoped subset), advisory CRAP, and advisory mutation — plus, in go and rust only,
the architecture boundary check itself (`arch`), which stays
`ci`/`pre-push`-only there (go's needs to fetch a module, rust's takes
cargo's build lock); python and bun's `arch` has neither constraint, so it
runs inside `check` too — so a green `check` predicts a green `ci`.

Quality subcommands also callable standalone: `complexity`, `crap`,
`acceptance`, `coverage` (Go also keeps `test-cov`), `mutation`, `arch`,
`arch-config-guard`, `gherkin-guard`, `suppressions`, and `deadcode` (python/bun).
`arch-config-guard` warns in `check`/`stop-hook` and fails
`pre-commit`/`pre-push`/`ci` when protected arch config paths changed unless
`HARNESS_ALLOW_ARCH_CONFIG=1` is set; its diff base falls back through
`origin/HEAD` → `origin/main` → `main` when no PR base ref is set (e.g. a
direct push to `main`). `gherkin-guard` warns in `check`/`stop-hook` and fails
`pre-commit`/`pre-push`/`ci` when a changed production-source file has no
accompanying `.feature` change, unless `HARNESS_ALLOW_NO_FEATURE=1` is set;
it skips silently when the repo has no `.feature` files anywhere yet, so
retrofitting the harness into a repo with no acceptance suite never blocks.
`deadcode` flags unused code and runs in `check` + `ci` +
`stop-hook`: python via vulture (app sources only, `--min-confidence 60`,
allowlist false positives in `vulture_allowlist.py`), bun via knip (unused
files/exports/deps, configured by `knip.json`, fetched on demand via
`bunx`). Go and rust have **no** `deadcode` target — their linters already
flag dead code (golangci-lint `unused`, clippy `dead_code` under
`-D warnings`). Python `test` runs the `TEST_COMMAND` module constant in
`harness.py` (stdlib `unittest` by default; a pytest repo edits that one line),
or `py_compile` over quality targets when no `tests/test*.py` files exist. Bun
`test`, `coverage`, `mutation`, and
`crap` warn and skip when no Bun test files exist. `complexity` runs
`uvx lizard@1.22.2` (CCN≤15, args≤8, length≤100) — all 4 templates, so
`uvx` must be on PATH; the same invocation with `-Eduplicate` counts duplicate
blocks. `crap` is **advisory by default** (warns with a green `⚠`, not a red
`✗`; pass `--enforce` to hard-fail) and runs in `ci`. `mutation` is scoped to
the changed source files (`--all` for the whole tree), prints the percentage
of mutants killed, and is advisory in the same way: it runs in `ci` after
coverage and prints `⚠` on a miss, never fails `ci`, and only the standalone
command with `--enforce` hard-fails against `mutation.min`. `test` in
`check`/`pre-commit` runs only the tests that map to changed modules (rule
under "Progressive adoption" below); `ci`, `pre-push`, and `--all` run the
whole suite.

`.harness-baseline` ratchets **seven** metric families (eight keys), not one.
The key names are fixed across all four languages:

| Family | Key | Direction | Meaning |
|---|---|---|---|
| complexity | `complexity.max_violations` | down | lizard warnings over CCN 15 / args 8 / length 100 |
| complexity | `duplication.max_blocks` | down | lizard `-Eduplicate` duplicate blocks |
| crap | `crap.max_violations` | down | functions over the CRAP threshold |
| arch | `arch.max_violations` | down | boundary violations reported by the arch tool |
| mutation | `mutation.min` | up | integer % of mutants killed on the last scoped run |
| coverage | `coverage.min` | up | coverage floor unless `--min=N` is passed |
| deadcode | `deadcode.max_findings` | down | python/bun only |
| suppressions | `suppressions.<kind>` | down | per-kind counts (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`) |

Growth past a floor fails `check`/`ci` (mutation excepted: advisory unless
`--enforce`). A **missing** file or a **missing key** makes that one gate
report-only: it passes with a `report-only: no .harness-baseline floor` label
and a hint to run the update. The only writer for all of them is
`suppressions --update-baseline`, which requires human sign-off — a naming
wart (`suppressions` is in the parity gate's non-allowlistable core command
list, so renaming it is a four-template change), deliberately deferred. Say
the command out loud when handing a repo over; nobody guesses it from the
name.

`--update-baseline` is **all-or-nothing**: a metric that errors aborts the
whole write and is named in the error, so nothing is half-written; a metric
that cannot be measured (no arch config, no tests, no deadcode tool) has its
key *dropped* with a warning, which is what stops the template's own
`coverage.min 100` from being inherited by an adopting repo. Unknown keys are
preserved. `mutation.min` is the one exception to "re-measure everything": a
mutation run costs minutes, so the automatic pass carries the existing value
through untouched and writes it only under `--update-baseline
--with-mutation`. The score is the same in all four languages:
`round(100 * killed / (killed + survived))`, killed = caught + timeout,
survived = ran and not detected; unviable, compile-error, skipped, and
not-covered mutants count in neither term, and `killed + survived == 0` is
"unavailable", i.e. report-only.

Property-based tests run inside the normal `test` step — no extra script.
Each template carries the language's PBT dev-dep (hypothesis / fast-check /
rapid / proptest) and seeds a property suite over its own CRAP and parser
helpers as the worked example. The behavior contract's law-like rule
points agents at that suite.

## Layer 2 — the behavior contract

Greenfield copies inherit it through `AGENTS.md`/`CLAUDE.md` and the runner.
For an existing repo, wire it **only when the user opts in**. Full porting + onboarding steps:
[behavior-contract.md](reference/behavior-contract.md). In short:

- `AGENTS.md` and `CLAUDE.md` both carry the `## Behavior contract`
  section. The two files hold the same content (the templates'
  `agents-md-drift` check enforces no drift).
- `arch-config-guard` protects the repo's architecture config at integration
  time: warning mode in `check`/`stop-hook`, strict mode in
  `pre-commit`/`pre-push`/`ci`, reviewed override via
  `HARNESS_ALLOW_ARCH_CONFIG=1`.
- `gherkin-guard` mechanically enforces the Gherkin-first rule at the same
  points: warning mode in `check`/`stop-hook`, strict mode in
  `pre-commit`/`pre-push`/`ci`, reviewed override via
  `HARNESS_ALLOW_NO_FEATURE=1`. It skips silently when the repo has no
  `.feature` files anywhere, so wiring it into a repo with no acceptance
  suite yet never blocks.

Of the four behavior-contract rules, two are mechanically enforced
(`arch-config-guard`, `gherkin-guard`) and two are instruction-only (task
sizing, human-owned commits) — see the table in
[behavior-contract.md](reference/behavior-contract.md).

After wiring Layer 2, **onboard the user** — state plainly that commit/push
ownership and task sizing are instruction-only, while arch-config changes and
Gherkin-first are blocked by the runner gates until reviewed.

## Adapt rules (existing repos)

### Progressive adoption

**The harness gates the change, not the codebase.** This is the rule that
makes a strict contract adoptable by a repo that has never had one. Never
weaken a gate to get a green first run — the design already handles it, via
two mechanisms split by whether a finding has a natural count:

- **Findings with no natural count** — a lint diagnostic, a format
  difference, a type error — are **diff-scoped**. `fix`, `format`, `lint`,
  `typecheck` resolve one git-derived file list (working tree + index +
  untracked + the diff against the base ref) and **skip with a warning when
  it is empty**. An empty scope must never widen to the whole tree; that
  fallback is the bug, not the feature. `--all` is the deliberate-audit
  escape hatch; `--base=<ref>` overrides the diff base for every
  changed-path consumer at once (spelled with a value — a bare `--base=` is
  an empty string and a silent no-op). In **python only**, scoping is
  line-level rather than file-level, with `--whole-file` to drop the line
  filter; bun/go/rust scope by file. `scripts/parity-gate.sh` compares
  subcommands, not flags, so it does not catch this — it is a deliberate
  boundary while python is the reference implementation, and a divergence to
  close during the port.
- **Which tests to run** is scoped the same way. In `check` and
  `pre-commit`, `test` runs only the changed test files plus the tests that
  map to changed source modules (per-language mapping in the language
  reference, e.g. `src/**/<mod>.py` → `tests/**/test_<mod>.py`). The
  change set comes from the stage: local stages (`check`, `pre-commit`,
  `post-edit`) use the uncommitted set (`git status --porcelain`); anything
  with a resolved base ref (`--base=<ref>`, `HARNESS_ARCH_BASE`,
  `GITHUB_BASE_REF`, then the `origin/HEAD` → `origin/main` → `main`
  fallbacks) uses `git diff --name-only <base>...HEAD`. An empty scope warns
  and skips, never widens. A changed source file that maps to no test gets
  one `⚠` line per file, never a failure, and the tests that do map still
  run. `ci`, `pre-push`, and `--all` always run the whole suite. The
  `git status` helper must never feed `ci`: there it yields an empty set and
  a green gate that tested nothing.
- **Findings with a natural count** — coverage percent, mutants killed,
  functions over the complexity threshold, duplicate blocks, CRAP offenders,
  arch boundary violations, deadcode findings, suppression counts — get a
  **floor that starts where the repo already is**, recorded in
  `.harness-baseline` under the keys in the table above. These stay
  whole-tree on purpose (mutation excepted, which is scoped because a run
  costs minutes): scoping a count makes it meaningless.
- **Architecture is adopted by baselining, never by deleting.** When the
  repo has module boundaries, derive the arch contract from the real package
  tree (the layers that exist, top down), keep the config, and let
  `suppressions --update-baseline` record today's violation count as
  `arch.max_violations`. Deleting or loosening the arch config to get a green
  first run throws away the only structural signal the harness has; a repo
  with genuinely no boundaries gets no config, and the gate warns and skips.

Consequence: first run is identical for greenfield and brownfield —
`suppressions --update-baseline`, then `check`, then `setup-hooks` — and
green in both, because the scoped gates see only the diff and every counted
metric sits exactly at its just-written floor. A 208k-line repo whose
day-one strict finding count is ~30,000 adopts on the same three commands as
an empty directory. Whole-tree absolute thresholds are what get a harness
deleted instead of adopted.

State the trade-off to the user rather than hiding it: **a green scoped run
is not a clean repo.** A pre-existing violation on a file the change does not
touch is not reported. The floors are what move the repo — hand the user
`/ratchet` (`skills/ratchet/`), which raises them one notch at a time by
adding tests only, never by touching production code.

Do not record progressive adoption as "skipped by decision". Nothing was
skipped; the gates are all on, aimed at the change.

### Merging

- Merge into existing `AGENTS.md` / `CLAUDE.md`, configs, and lockfiles.
  Never overwrite. Never reduce an existing contract file to a stub —
  AGENTS.md-consuming tools read it literally, so a stub delivers no
  contract.
- Both `AGENTS.md` and `CLAUDE.md` should hold the same full content:
  - Both files exist → merge the contract into both; wire the drift check.
  - Only `AGENTS.md` exists → add `CLAUDE.md` with identical content.
  - Only `CLAUDE.md` exists → add `AGENTS.md` with identical content.
  - Neither exists → create both.
  - **Look at the two files with `ls -la` first.** Byte-identity is a
    packaging convention (Claude reads one, Codex the other), not a quality
    property, and real repos break the assumption: one test repo tracks
    `agents.md` lowercase (on a case-insensitive filesystem `AGENTS.md`
    writes straight *through* to it), another tracks `CLAUDE.md` as a
    **symlink** to `AGENTS.md`. When the repo's own `AGENTS.md` legitimately
    differs from the contract, set `HARNESS_ALLOW_AGENTS_MD_DRIFT=1` — that
    is the documented retrofit path, not an escape hatch — rather than
    welding the two documents together.
- Keep the repo's task runner; reuse its invocation prefix in the
  command list.
- Template runner files (`harness.py`, `harness.ts`, `harness.go`,
  `cargo harness`) are for greenfield/copy flow — not the default for
  repos that already have a runner.
- Layer 2 is opt-in: never wire behavior-contract instructions or the
  arch-config/gherkin-first integration guards into a repo that did not ask
  for them.
- The contract's law-like rule (property tests) needs the language's PBT
  dev-dep: hypothesis (python), fast-check (bun), rapid (go), proptest
  (rust). Wire it when porting the contract — or on the first law-like
  change — and model the suite on the template's seeded example.

## Runner output contract

Quiet by default. Mirror `~/Code/harness-templates/python/harness.py` `run()`:

- One short line per step on success.
- Capture stdout/stderr; print only on failure.
- On failure: print failing command + full captured output, then exit.
- `--verbose` escape hatch streams raw command output.
- Zero external deps — stdlib/runtime only.

## Verify (end-to-end)

Layer 1:

1. `check` passes from a fresh tree.
2. `ci` does not mutate tracked files (`git status` clean after).
3. Pre-commit hook fires on a staged change (`.git/hooks/pre-commit` exists)
   and re-stages the files it fixes.
4. `audit` passes.
5. `stop-hook` runs via Stop hook and includes post-edit formatting (Claude/Codex hooks wired per
   [settings-json.md](reference/settings-json.md)). Force a failure and confirm
   the Claude Stop-hook command exits 2 with a failure summary on stderr — a
   non-2 exit is silently swallowed and the agent never sees it.
6. Growth past any `.harness-baseline` floor fails (mutation: warns);
   `suppressions --update-baseline` re-measures every key except
   `mutation.min`, which needs `--with-mutation`; a missing key is
   report-only.
7. `check` with one changed source file runs only its mapped tests and
   prints a `⚠` per changed source file with no mapped test; `check --all`
   and `ci` run the whole suite.
8. `arch-config-guard` warns in `check`/`stop-hook` and fails
   `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set.
9. `gherkin-guard` warns in `check`/`stop-hook` and fails
   `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_NO_FEATURE=1` is set, and
   skips silently when the repo has no `.feature` files anywhere.
10. Runner imports nothing outside stdlib/runtime.

Layer 2 (only if wired):

11. `AGENTS.md` and `CLAUDE.md` include the same full behavior contract text.
12. Commit/push ownership and task-sizing rules are present as instructions;
    Gherkin-first and arch-config review are present as instructions **and**
    enforced by `gherkin-guard`/`arch-config-guard`.
