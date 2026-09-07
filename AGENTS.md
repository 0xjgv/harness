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
hooks run `make stop-hook`, and root Git hooks run `make pre-commit` /
`make pre-push`. Each template subdirectory remains a fully independent copy-paste
unit. The provisioner bundle is intentionally copied byte-for-byte across surfaces;
there is no runtime inheritance between `python/`, `bun/`, `go/`, `rust/`, and
`monorepo/`.

## Commands (root level)

The root `Makefile` owns deterministic workspace convergence and repo-level
dogfooding:

| Command | Contract |
|---|---|
| `make workspace` | Provision the detected profile union, locked dependencies, skills, safe root hooks, verification, `make check`, and final tracked-tree verification |
| `make workspace OFFLINE=1` | Repeat convergence without network access from warm exact-tool and dependency caches |
| `make deps` | Call every detected template's lock-preserving `make deps` exactly once in lexical order |
| `make bootstrap` / `make setup` | Compatibility aliases for `make workspace` |
| `make ci` | Run the root read-only gate across every detected template |
| `make check` | Verify deployed skill drift, root instruction parity, and protected architecture config |
| `make setup-hooks` | Delegate collision-safe root Git-hook installation and Stop verification to the provisioner |
| `make sync-skills` | Copy the current canonical `skills/harness/` package to `~/.claude/skills/harness/` and `~/.agents/skills/harness/` |
| `make agents-md-drift` | Fail if root `AGENTS.md` differs from `CLAUDE.md` |
| `make sync-agents-md` | Copy root `CLAUDE.md` to `AGENTS.md` |
| `make arch-config-guard ARGS=--warn` | Warn on protected architecture-config changes |
| `make stop-hook` | Sync current derived root docs/skills, warn on architecture config, and dispatch into dirty templates |
| `make help` | List targets |

Workspace supports macOS and glibc Linux on `x86_64` and `arm64`. The bootstrap
layer is Make, Bash, Git, curl, tar, Info-ZIP unzip, and a SHA-256 utility. `HOME`
must be writable; Rust additionally requires `cc`. A Git worktree is required.
Tracked/index state must be clean; untracked files are preserved. It
never uses `sudo`, Homebrew, or shell-profile edits. Exact tools live under
`~/.local/share/harness/tools/<tool>/<version>`; ambient versions are ignored.

Workspace preflights the platform, bootstrap commands, manifest, input digests,
Git state, skill source, Stop wiring, and both Git-hook destinations before any
download or hook/skill mutation. It installs tools and locked dependencies before
deploying skills or hooks. It verifies exact tools and frozen state, runs the normal
auto-fixing `make check`, then fails with changed paths if tracked/index state is no
longer clean; it never reverts those changes.

Root `.harness/workspace.sh`, `.harness/workspace.lock`, and all eight native input
files are canonical. The same bytes and modes are copied under `.harness/` in all
five templates. `bash tests/provisioner_drift_test.sh` guards the complete path set,
bytes, and modes. The native inputs are `python-downloads.json`,
`python-tools.lock`, `bun-tools/package.json`, `bun-tools/bun.lock`,
`go-tools/go.mod`, `go-tools/go.sum`, `rust-dist.lock`, and
`cargo-modules.lock`.

**After editing anything under `skills/harness/`, always run `make sync-skills`**, then
`make check` to confirm no drift remains. After editing root `CLAUDE.md`, run
`make sync-agents-md`; the root `post-edit` helper also does this automatically during
`make stop-hook`.

## Commands (inside a template)

Each template implements the same **5-script contract** independently, via its own
zero-dependency task runner (`harness.py` / `harness.ts` / `harness.go` / `harness.rs`).
There is no cross-template abstraction for this — each runner is stdlib/runtime-only by
design, so logic is duplicated per language on purpose.

```bash
cd python && make check     # fix, format, typecheck, test, suppression ratchet
cd bun    && make check
cd go     && make check
cd rust   && make check
cd monorepo && make check           # dispatches check to every subproject copied inside it
```

Make is the normal command boundary. When harness-specific arguments are needed,
use the template's provisioner with the exact runner form:

| Profile | Exact direct harness boundary |
|---|---|
| Python | `.harness/workspace.sh exec python -- uv run --frozen --no-sync harness <command> [args]` |
| Bun | `.harness/workspace.sh exec bun -- bun harness.ts <command> [args]` |
| Go | `.harness/workspace.sh exec go -- go run -mod=readonly harness.go <command> [args]` |
| Rust | `.harness/workspace.sh exec rust -- cargo run --quiet --locked --bin harness -- <command> [args]` |

| Script | When | Does | Fixes code? |
|---|---|---|---|
| `check` | after edits | fix, format, typecheck, test, suppression ratchet | yes |
| `pre-commit` | git pre-commit hook | same, staged files only | yes |
| `pre-push` | git pre-push hook | read-only: lint, format check, acceptance, arch, over the whole tree, in parallel | no |
| `ci` | CI pipeline | read-only gates (lint, typecheck, dep audit, complexity, deadcode, acceptance, arch) in parallel, then coverage + advisory CRAP | no |
| `audit` | CI pipeline | dependency vulnerability audit | no |
| `post-edit` | Stop hook helper | format changed source files | yes |
| `stop-hook` | agent Stop hook | `post-edit` + complexity (+ deadcode where shipped) | yes |

Other standalone subcommands every template exposes: `complexity`, `crap`, `acceptance`,
`coverage` (Go also keeps `test-cov`), `mutation`, `arch`, `suppressions`,
`agents-md-drift`, and `sync-agents-md`. Python and Bun additionally expose `deadcode` (vulture / knip); Go and
Rust rely on their linters (`golangci-lint unused`, clippy `dead_code`) instead of a
separate target. `crap` is advisory by default (`--enforce` to hard-fail). Full command
tables with exact flags live in each template's own `CLAUDE.md` — read that file before
working inside a template rather than re-deriving commands here.

To run a single test, keep the managed boundary and scope the native runner:

```bash
cd python && .harness/workspace.sh exec python -- uv run --frozen --no-sync python -m unittest tests.test_crap
cd bun    && .harness/workspace.sh exec bun -- bun test tests/crap.test.ts
cd go     && .harness/workspace.sh exec go -- go test -mod=readonly ./crap/...
cd rust   && .harness/workspace.sh exec rust -- cargo test --locked --test smoke
```

The harness `check`/`ci` targets always run the full suite.

Each template ships `.github/workflows/ci.yml` with the same two commands used
locally: `make workspace`, then `make ci`.

Only `.harness/workspace.sh install-hooks` writes Git hooks. `make setup-hooks` and
the runners' compatibility command delegate to it; do not add hook-writing logic to
language runners. Unknown existing hooks cause preflight refusal without modifying
either destination. Only exact legacy harness shims migrate. Installed Git hooks and
the checked-in Claude/Codex Stop hooks enter the Git root and invoke
`make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the managed
environment.

## Architecture

**Templates are independent, not inherited.** `python/`, `bun/`, `go/`, `rust/` each
carry their own linter, type checker, test runner, security lint rules, dependency
auditor, directly provisioned lizard complexity gate, and CRAP advisory
gate. `monorepo/` is different in kind: it's a thin Make dispatcher with **no** lint/
format/test logic of its own — it discovers subprojects by the presence of
`harness.{ts,py,go}` / `Cargo.toml` in top-level dirs and forwards `check`/`ci`/
`pre-push`/etc. to each subproject's own harness (see `monorepo/Makefile`'s
`lang_of`/`runner_of` dispatch table). `monorepo/` is meant to have single-language
templates copied inside it as subprojects (`cp -r python/ api`), not edited standalone.

The managed manifest provisions lizard and the direct Python vulture/pip-audit,
Bun knip, Go govulncheck/go-arch-lint/gremlins, and Rust
cargo-audit/cargo-llvm-cov/cargo-modules tools. Runners invoke these managed commands
directly. They must not launch tools through `uvx`, `bunx`, version-suffixed `go run`,
ambient installers, or system-tool fallbacks. Cargo-mutants is intentionally absent;
Rust `make mutation` reports its deterministic advisory skip.

**Two-layer contract, shipped per template:**
- **Layer 1 — quality harness** (always on): the 5-script contract above.
- **Layer 2 — behavior contract** (greenfield: automatic; ported into an existing repo:
  opt-in only): instruction text in `AGENTS.md` and `CLAUDE.md` for task-sizing,
  human-owned commits, and Gherkin-first behavior changes, plus a portable
  `arch-config-guard` that warns during `check`/`stop-hook` and blocks
  `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after
  review. Full design: `skills/harness/reference/behavior-contract.md`.

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
5-script contract, a zero-dependency runner, byte-identical `AGENTS.md`/`CLAUDE.md`,
security-focused lint rules, a dependency audit wired into `ci`, Stop-hook wiring
(`.claude/settings.json` + `.codex/hooks.json`), and get added to the root `README.md`
tables. Use `python/` or `go/` as the reference implementation.

## Design principles (apply to every template's runner)

- Zero external dependencies in the runner — stdlib/runtime APIs only.
- Quiet by default — one line per successful step; full output only on failure;
  `--verbose` is the escape hatch.
- `check`/`pre-commit`/`post-edit` fix what they can; `pre-push`/`ci`/`audit` are
  strictly read-only.
