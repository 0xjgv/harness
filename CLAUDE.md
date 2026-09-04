# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Three things live here, and they're easy to conflate:

1. **Five project templates** (`python/`, `bun/`, `go/`, `rust/`, `monorepo/`) — each a
   complete, self-contained starting point for a new project, with a zero-dependency
   quality harness baked in.
2. **The `harness` skill source** (`skills/harness/`) — the canonical instructions Claude
   Code / Codex use to bootstrap or audit *other* repos with this same contract.
3. **The `ratchet` skill source** (`skills/ratchet/`) — raises a harness-adopting repo's
   `.harness-baseline` floors one notch per run by writing tests only.

Both skills get deployed (copied) to `~/.claude/skills/<name>/` and
`~/.agents/skills/<name>/`.

This root directory is the meta-repo, not one of the templates. It dogfoods a small
meta-harness: root `AGENTS.md` and `CLAUDE.md` are byte-identical, root Stop hooks run
`make stop-hook`, and root git hooks run `make pre-commit` / `make pre-push`. Each
template subdirectory is a fully independent copy-paste unit; there is no shared code or
dependency between `python/`, `bun/`, `go/`, `rust/`, and `monorepo/`.

## Commands (root level)

The root `Makefile` manages repo-level dogfooding and skill deployment:

- `make check` — `skills-drift` + `agents-md-drift` + `parity`, then warn on protected
  arch config changes
- `make parity` — fail if `python`/`bun`/`go`/`rust` harness command surfaces have
  drifted apart (`scripts/parity-gate.sh`)
- `make skills-drift` — fail if `~/.claude/skills/{harness,ratchet}/` or
  `~/.agents/skills/{harness,ratchet}/` differ from `skills/<name>/`
- `make sync-skills` — copy `skills/<name>/` → both deployed locations (file list is
  `HARNESS_FILES` / `RATCHET_FILES` in the Makefile; a new reference doc must be added
  there or it will not deploy)
- `make agents-md-drift` / `make sync-agents-md` — check / regenerate root `AGENTS.md`
  from `CLAUDE.md`
- `make arch-config-guard ARGS=--warn` — advisory arch config guard (`--staged` scopes
  to the index)
- `make stop-hook` — root Stop hook: `post-edit` (sync derived docs/skills, format dirty
  templates), warn on arch config changes, then dispatch `stop-hook` into dirty templates
- `make ci` — read-only: strict arch-config guard, `agents-md-drift`, `skills-drift`,
  then every template's own `ci`. Mirrors `.github/workflows/ci.yml`, which runs
  `make check` then `make ci` in one job with `HARNESS_SKIP_SKILLS_DRIFT=1` set (the
  deployed skill copies live under `$HOME`, which doesn't exist on a CI runner)
- `make check-dirty` — run `check` only in templates with working-tree changes
- `make audit` — dependency audit across all templates
- `make setup-hooks` — install root `.git/hooks/pre-commit` and `pre-push`, then verify
  root Claude/Codex Stop hook wiring
- `make list` / `make help`

**After editing anything under `skills/`, always run `make sync-skills`**, then
`make check` to confirm no drift remains. After editing root `CLAUDE.md`, run
`make sync-agents-md`; the root `post-edit` helper also does both automatically during
`make stop-hook`. Never hand-edit `AGENTS.md`.

Template runners are dispatched by detecting `harness.ts` / `harness.py` / `harness.go`
/ `Cargo.toml` in top-level dirs (`SUBPROJECTS` in the Makefile). The `stop-hook` recipe
must keep exiting 2 on failure — Claude Code only feeds a Stop hook's stderr back to the
model on exit 2; do not wrap it or `_run`/`post-edit` in `|| true`.

## Commands (inside a template)

Each template implements the same **seven-stage contract** independently, via its own
zero-dependency task runner (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`).
There is no cross-template abstraction — each runner is stdlib/runtime-only by design, so
logic is duplicated per language on purpose. `scripts/parity-gate.sh` (`make parity`)
statically checks that the four runners' command surfaces stay in sync with each other
and with their own `Makefile` / `CLAUDE.md` / `package.json`; any deliberate divergence
must be added to its `ALLOWLIST` with a reason.

```bash
cd python && uv run harness check   # fix, format, typecheck, test, every offline/fast/no-lock gate
cd bun    && bun run check          # (or: bun harness.ts check)
cd go     && go run harness.go check
cd rust   && cargo harness check
cd monorepo && make check           # dispatches check to every subproject copied inside it
```

| Stage | When | Does | Fixes code? |
|---|---|---|---|
| `check` | after edits | fix, format, typecheck, test, plus every other gate that is offline, fast, and takes no build lock (complexity, acceptance, deadcode where shipped, a lockfile check in Python/Bun, `go mod tidy -diff` in Go, and — Python/Bun only — `arch`); warns on `arch-config-guard` and `gherkin-guard`; checks agents-md drift; ratchets suppressions | yes |
| `pre-commit` | git pre-commit hook | same, staged files only, then re-stages (`git add`) the files it fixed | yes |
| `pre-push` | git pre-push hook | read-only: lint, format check, acceptance, arch, over the whole tree, in parallel; strict `arch-config-guard` + `gherkin-guard` | no |
| `ci` | CI pipeline | read-only gates (lint, typecheck, dep audit, complexity, deadcode, acceptance, arch) in parallel, then coverage + advisory CRAP; strict `arch-config-guard` + `gherkin-guard` | no |
| `audit` | CI pipeline | dependency vulnerability audit | no |
| `post-edit` | Stop hook helper | format changed source files | yes |
| `stop-hook` | agent Stop hook | `post-edit` + complexity (+ deadcode where shipped); exits 2 with a stderr failure summary so Claude's Stop hook blocks and feeds the failure back to the agent | yes |

Invariant every runner documents: `ci` minus `check` is only the network dependency
audit, coverage, and advisory CRAP — plus, in Go and Rust only, `arch` (Go's needs to
fetch a module, Rust's takes cargo's build lock) — so a green `check` predicts a green `ci`.

Core subcommands every template must expose (`CORE_COMMANDS` in `scripts/parity-gate.sh`,
never allowlist-exempt): the seven stages plus `complexity`, `crap`, `acceptance`,
`coverage`, `mutation`, `arch`, `arch-config-guard`, `gherkin-guard`, `suppressions`,
`agents-md-drift`, `sync-agents-md`, `setup-hooks`, `clean`. Python and Bun additionally
expose `deadcode` (vulture / knip); Go keeps a `test-cov` alias. `crap` is advisory by
default (`--enforce` to hard-fail). `gherkin-guard` skips silently when the template has
no `.feature` files anywhere. Full command tables with exact flags live in each template's
own `CLAUDE.md` — read that file before working inside a template rather than re-deriving
commands here.

To run a single test, use the template's native test runner scoped to a file/pattern
(e.g. `uv run python -m unittest tests.test_crap`, `bun test tests/crap.test.ts`,
`go test ./crap/...`, `cargo test --test smoke`) — the harness `check`/`ci` targets
always run the full suite.

Each template also ships its own `.github/workflows/ci.yml` that runs that template's
`harness ci` — the local gate and the remote gate are the same command by design.

## Architecture

**Templates are independent, not inherited.** `python/`, `bun/`, `go/`, `rust/` each
carry their own linter, type checker, test runner, security lint rules, dependency
auditor, complexity gate (`lizard` via `uvx`, all four languages), and CRAP advisory
gate. `monorepo/` is different in kind: a thin Make dispatcher with **no** lint/format/
test logic of its own — it discovers subprojects by the presence of
`harness.{ts,py,go}` / `Cargo.toml` in top-level dirs and forwards `check`/`ci`/
`pre-push`/etc. to each subproject's own harness (see `monorepo/Makefile`'s
`lang_of`/`runner_of` dispatch table, mirrored in the root Makefile). `monorepo/` is
meant to have single-language templates copied inside it as subprojects
(`cp -r python/ api`), not edited standalone.

**Two-layer contract, shipped per template:**
- **Layer 1 — quality harness** (always on): the seven-stage contract above.
- **Layer 2 — behavior contract** (greenfield: automatic; ported into an existing repo:
  opt-in only): instruction text in `AGENTS.md` and `CLAUDE.md` for task-sizing,
  human-owned commits, and Gherkin-first behavior changes, plus two portable,
  mechanically enforced guards — `arch-config-guard` and `gherkin-guard` — that warn
  during `check`/`stop-hook` and block `pre-commit`/`pre-push`/`ci` unless
  `HARNESS_ALLOW_ARCH_CONFIG=1` / `HARNESS_ALLOW_NO_FEATURE=1` is set after
  review. Full design: `skills/harness/reference/behavior-contract.md`.

**Brownfield adoption: the harness gates the change, not the codebase.** Findings with
no natural count (lint, format, type errors) are diff-scoped to changed lines; findings
with a natural count (coverage, complexity, CRAP, deadcode, suppressions) get a floor in
`.harness-baseline` that starts where the repo already is, written by
`suppressions --update-baseline` (all-or-nothing) and raised only via the `ratchet`
skill. Today only `python/harness.py` implements the diff scoping (`--all`, `--base=<ref>`,
`--whole-file`); `bun`, `go`, `rust` gate the whole tree. A scoped gate that widens to
the whole tree on an empty scope is a defect, not a stricter variant. Audit procedure:
`skills/harness/reference/adoption-checklist.md`.

**`AGENTS.md`/`CLAUDE.md` are byte-identical within each template**, enforced by that
template's own `agents-md-drift` harness command and fixed by `sync-agents-md`
(`CLAUDE.md` is the source; `AGENTS.md` is derived). Claude Code reads `CLAUDE.md`,
Codex/other AGENTS.md-consuming tools read `AGENTS.md`. When editing a template's agent
instructions, edit `CLAUDE.md` and run `sync-agents-md`, never hand-edit `AGENTS.md`.

Precedence: a template's `CLAUDE.md` wins for that template's exact commands; the root
README owns the cross-template contract; skill references under `skills/` are derived
guidance.

**`skills/` is the single source of truth for both skills** deployed to two locations
each. Edit only the canonical copy in this repo, then `make sync-skills`; `make check`
(`skills-drift`) guards against the deployed copies silently diverging. The skills' own
reference docs (`reference/<lang>.md`, `behavior-contract.md`, `settings-json.md`,
`adoption-checklist.md`) describe how an agent should bootstrap, port, or audit the
contract in an arbitrary repo — they are documentation *about* this repo's contract,
not code that runs here. Both skills tell the agent to read files under
`~/Code/harness-templates/` and never edit them.

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

<important if="the task changes user-visible template behavior">
- Workflow: write or extend a `.feature` scenario in the affected template when that template has acceptance coverage → get human approval → write step definitions → write implementation.
- If the behavior is law-like (formula, parser, codec, round-trip, invariant), also write a property test with the affected template's PBT tool (hypothesis / fast-check / rapid / proptest), not just examples.
- Refactors, typo fixes, docs-only changes, dependency bumps, and internal cleanup are NOT user-visible template behavior changes. You MAY proceed without a new `.feature`, but you MUST state in your first response that the change is non-behavioral and why.
- If it is unclear whether a task changes user-visible template behavior, ASK before editing source.
</important>

<important if="you want to edit a template's arch config">
- Each language template has its own arch config: `.importlinter` (python), `.dependency-cruiser.json` (bun), `.go-arch-lint.yml` (go), `arch.toml` (rust).
- Do not silently edit an arch config to silence a violation. Architectural violations imply a design decision — surface them to the human.
- The root and template harnesses warn about arch config changes during `check`/`stop-hook` and block `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review.
</important>

## Adding a new language template

Follow the checklist in `CONTRIBUTING.md` — new template must implement the full
seven-stage contract (including `gherkin-guard`), a zero-dependency runner,
byte-identical `AGENTS.md`/`CLAUDE.md`, security-focused lint rules, a dependency audit
wired into `ci`, a `stop-hook` that exits 2 with a stderr summary on failure, Stop-hook
wiring (`.claude/settings.json` + `.codex/hooks.json`), an entry in
`scripts/parity-gate.sh`'s expectations, and get added to the root `README.md` tables.
Use `python/` or `go/` as the reference implementation.

## Design principles (apply to every template's runner)

- Zero external dependencies in the runner — stdlib/runtime APIs only.
- Quiet by default — one line per successful step; full output only on failure;
  `--verbose` is the escape hatch.
- `check`/`pre-commit`/`post-edit` fix what they can; `pre-push`/`ci`/`audit` are
  strictly read-only.
