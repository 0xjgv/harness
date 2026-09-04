# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two things live here, and they're easy to conflate:

1. **Five project templates** (`python/`, `bun/`, `go/`, `rust/`, `monorepo/`) — each a
   complete, self-contained starting point for a new project, with a zero-dependency
   quality harness baked in.
2. **The `harness` skill source** (`skills/harness/`) — the canonical instructions Claude
   Code / Codex use to bootstrap *other* repos with this same contract. It gets deployed
   (copied) to `~/.claude/skills/harness/` and `~/.agents/skills/harness/`.

This root directory is the meta-repo, not one of the templates. It now dogfoods a
small meta-harness: root `AGENTS.md` and `CLAUDE.md` are byte-identical, root Stop
hooks run `make stop-hook`, and root git hooks can run `make pre-commit` /
`make pre-push`. Each template subdirectory remains a fully independent copy-paste
unit; there is no shared code or dependency between `python/`, `bun/`, `go/`,
`rust/`, and `monorepo/`.

## Commands (root level)

The root `Makefile` manages repo-level dogfooding and skill deployment:

- `make check` — fail if `~/.claude/skills/harness/` or
  `~/.agents/skills/harness/` differ from the canonical `skills/harness/`, fail if
  root `AGENTS.md` differs from `CLAUDE.md`, fail if the four language templates'
  command surfaces have drifted apart (`make parity`), and warn on protected arch
  config changes
- `make parity` — fail if `python`/`bun`/`go`/`rust` harness command surfaces have
  drifted apart (`scripts/parity-gate.sh`); a prerequisite of `make check`
- `make sync-skills` — copy `skills/harness/*.md` → both deployed locations
- `make agents-md-drift` — fail if root `AGENTS.md` differs from `CLAUDE.md`
- `make sync-agents-md` — copy root `CLAUDE.md` → `AGENTS.md`
- `make arch-config-guard ARGS=--warn` — warn on protected arch config changes
- `make stop-hook` — root Stop hook: sync derived root docs/skills when needed, warn on
  arch config changes, and dispatch `stop-hook` into dirty language templates
- `make ci` — root read-only verification: strict arch-config guard, `agents-md-drift`,
  `skills-drift`, then every template's own `ci`. Mirrors `.github/workflows/ci.yml`,
  which runs `make check` then `make ci` in one job with
  `HARNESS_SKIP_SKILLS_DRIFT=1` set (the deployed skill copies live under `$HOME`,
  which doesn't exist on a CI runner)
- `make setup-hooks` — install root `.git/hooks/pre-commit` and `.git/hooks/pre-push`,
  then verify root Claude/Codex Stop hook wiring
- `make help` — list targets

**After editing anything under `skills/harness/`, always run `make sync-skills`**, then
`make check` to confirm no drift remains. After editing root `CLAUDE.md`, run
`make sync-agents-md`; the root `post-edit` helper also does this automatically during
`make stop-hook`.

## Commands (inside a template)

Each template implements the same **seven-stage contract** independently, via its own
zero-dependency task runner (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`).
There is no cross-template abstraction for this — each runner is stdlib/runtime-only by
design, so logic is duplicated per language on purpose. `scripts/parity-gate.sh`
(`make parity` at the root) statically checks that the four runners' command surfaces
stay in sync with each other and with their own `Makefile`/`CLAUDE.md`/`package.json`.

```bash
cd python && uv run harness check   # fix, format, typecheck, test, every offline/fast/no-lock gate
cd bun    && bun run check          # (or: bun harness.ts check)
cd go     && go run harness.go check
cd rust   && cargo harness check
cd monorepo && make check           # dispatches check to every subproject copied inside it
```

| Stage | When | Does | Fixes code? |
|---|---|---|---|
| `check` | after edits | fix, format, typecheck, test, plus every other gate that is offline, fast, and takes no build lock (complexity, acceptance, deadcode where shipped, a lockfile check in Python/Bun, `go mod tidy -diff` in Go, and — Python/Bun only — the architecture boundary check `arch` itself); warns on `arch-config-guard` and `gherkin-guard`; checks agents-md drift; ratchets suppressions | yes |
| `pre-commit` | git pre-commit hook | same, staged files only, then re-stages (`git add`) the files it fixed | yes |
| `pre-push` | git pre-push hook | read-only: lint, format check, acceptance, arch, over the whole tree, in parallel; strict `arch-config-guard` + `gherkin-guard` | no |
| `ci` | CI pipeline | read-only gates (lint, typecheck, dep audit, complexity, deadcode, acceptance, arch) in parallel, then coverage + advisory CRAP; strict `arch-config-guard` + `gherkin-guard` | no |
| `audit` | CI pipeline | dependency vulnerability audit | no |
| `post-edit` | Stop hook helper | format changed source files | yes |
| `stop-hook` | agent Stop hook | `post-edit` + complexity (+ deadcode where shipped); exits 2 with a stderr failure summary so Claude's Stop hook blocks and feeds the failure back to the agent | yes |

Invariant every runner documents: `ci` minus `check` is only the network dependency
audit, coverage, and advisory CRAP — plus, in Go and Rust only, the architecture
boundary check itself (`arch`), which stays `ci`/`pre-push`-only there (Go's needs to
fetch a module, Rust's takes cargo's build lock); Python and Bun's `arch` has neither
constraint, so it runs inside `check` too — so a green `check` predicts a green `ci`.

Other standalone subcommands every template exposes: `complexity`, `crap`, `acceptance`,
`coverage` (Go also keeps `test-cov`), `mutation`, `arch`, `arch-config-guard`,
`gherkin-guard`, `suppressions`, `agents-md-drift`, `sync-agents-md`, `setup-hooks`.
`gherkin-guard` warns in `check`/`stop-hook` and blocks `pre-commit`/`pre-push`/`ci`
when a changed production-source file has no accompanying `.feature` change
(`HARNESS_ALLOW_NO_FEATURE=1` overrides after review); it skips silently when the
template has no `.feature` files anywhere. Python and Bun additionally expose `deadcode` (vulture / knip); Go and
Rust rely on their linters (`golangci-lint unused`, clippy `dead_code`) instead of a
separate target. `crap` is advisory by default (`--enforce` to hard-fail). Full command
tables with exact flags live in each template's own `CLAUDE.md` — read that file before
working inside a template rather than re-deriving commands here.

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
gate. `monorepo/` is different in kind: it's a thin Make dispatcher with **no** lint/
format/test logic of its own — it discovers subprojects by the presence of
`harness.{ts,py,go}` / `Cargo.toml` in top-level dirs and forwards `check`/`ci`/
`pre-push`/etc. to each subproject's own harness (see `monorepo/Makefile`'s
`lang_of`/`runner_of` dispatch table). `monorepo/` is meant to have single-language
templates copied inside it as subprojects (`cp -r python/ api`), not edited standalone.

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
no natural count (lint, format, type errors, and which tests to run) are diff-scoped:
local stages (`check`, `pre-commit`, `post-edit`) scope to the uncommitted set, anything
with a resolved base ref (`--base=<ref>`, `HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`) to
`git diff --name-only <base>...HEAD`; an empty scope warns and skips, never widens;
`test` warns once per changed source file with no mapped test; `ci`, `pre-push`, and
`--all` run the whole tree. Findings with a natural count get a floor in
`.harness-baseline` that starts where the repo already is — seven families under fixed
key names: `complexity.max_violations`, `duplication.max_blocks`,
`crap.max_violations`, `arch.max_violations`, `mutation.min`, `coverage.min`,
`deadcode.max_findings`, `suppressions.*` — written only by
`suppressions --update-baseline` (all-or-nothing; drops an unmeasurable key with a
warning; `mutation.min` only under `--with-mutation`), report-only while a key is
missing, and raised only via the `ratchet` skill. `mutation` is advisory in `ci` after
coverage and hard-fails only standalone with `--enforce`. Arch is adopted by deriving a
contract from the real package tree and baselining its violations, never by deleting
the config. `python/harness.py` is the reference implementation (it also has
`--whole-file`, line- to file-level, which stays Python-only); `bun`, `go`, and `rust`
gain the same flags and floors as each language's unit lands. A scoped gate that widens
to the whole tree on an empty scope is a defect, not a stricter variant. Audit
procedure: `skills/harness/reference/adoption-checklist.md`.

**`AGENTS.md`/`CLAUDE.md` are byte-identical within each template**, enforced by that
template's own `agents-md-drift` harness command and fixed by `sync-agents-md`
(`CLAUDE.md` is the source; `AGENTS.md` is derived). Claude Code reads `CLAUDE.md`,
Codex/other AGENTS.md-consuming tools read `AGENTS.md` — same content, two filenames.
When editing a template's agent instructions, edit `CLAUDE.md` and run `sync-agents-md`,
never hand-edit `AGENTS.md` directly.

Precedence: a template's `CLAUDE.md` wins for that template's exact commands; the root
README owns the cross-template contract; skill references under `skills/harness/` are
derived guidance.

**`skills/harness/` is the single source of truth for the bootstrapping skill** deployed
to two locations (`~/.claude/skills/harness/`, `~/.agents/skills/harness/`). Edit only
the canonical copy in this repo, then `make sync-skills`; `make check` (skills-drift)
guards against the deployed copies silently diverging. The skill's own reference docs
(`reference/<lang>.md`, `reference/behavior-contract.md`, `reference/settings-json.md`)
describe how an agent should bootstrap or port the contract into an arbitrary repo — they
are documentation *about* this repo's contract, not code that runs here.

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
