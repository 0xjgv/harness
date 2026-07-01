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

This root directory is the meta-repo — it has no `AGENTS.md`/`CLAUDE.md` of its own and
is not itself one of the templates. Each template subdirectory is a fully independent
copy-paste unit; there is no shared code or dependency between `python/`, `bun/`, `go/`,
`rust/`, and `monorepo/`.

## Commands (root level)

The root `Makefile` only manages the skill-deployment drift, nothing else:

- `make check` (= `make skills-drift`) — fail if `~/.claude/skills/harness/` or
  `~/.agents/skills/harness/` differ from the canonical `skills/harness/` in this repo
- `make sync-skills` — copy `skills/harness/*.md` → both deployed locations
- `make help` — list targets

**After editing anything under `skills/harness/`, always run `make sync-skills`**, then
`make check` to confirm no drift remains.

## Commands (inside a template)

Each template implements the same **5-script contract** independently, via its own
zero-dependency task runner (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`).
There is no cross-template abstraction for this — each runner is stdlib/runtime-only by
design, so logic is duplicated per language on purpose.

```bash
cd python && uv run harness check   # fix, format, typecheck, test, suppression ratchet
cd bun    && bun run check          # (or: bun harness.ts check)
cd go     && go run harness.go check
cd rust   && cargo harness check
cd monorepo && make check           # dispatches check to every subproject copied inside it
```

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
`agents-md-drift`, `sync-agents-md`, `setup-hooks`. Python and Bun additionally expose `deadcode` (vulture / knip); Go and
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
- **Layer 1 — quality harness** (always on): the 5-script contract above.
- **Layer 2 — behavior contract** (greenfield: automatic; ported into an existing repo:
  opt-in only): hooks under each template's `.claude/scripts/` that mechanically enforce
  task-sizing, human-owned commits, Gherkin-first for behavior changes, and arch-config
  write-protection. The contract text lives in `## Behavior contract` in both
  `AGENTS.md` and `CLAUDE.md`; the hooks (`pre-bash-gate.sh`, `pre-edit-gate.sh`,
  `session-start.sh`, `ups-classify.sh`) make it survive `/clear`/`/compact`/resume.
  Full design: `skills/harness/reference-behavior-contract.md`.

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
(`reference-<lang>.md`, `reference-behavior-contract.md`, `reference-settings-json.md`)
describe how an agent should bootstrap or port the contract into an arbitrary repo — they
are documentation *about* this repo's contract, not code that runs here.

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
