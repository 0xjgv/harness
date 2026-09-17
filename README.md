# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The 5-Script Contract

Every template implements the 5 required scripts plus a `stop-hook` target
used by the agent Stop hook:

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test, suppression ratchet | Yes |
| `pre-commit` | Git pre-commit hook | Staged files only — fix, format, typecheck; syncs a staged `CLAUDE.md` into `AGENTS.md`. No tests | Yes |
| `pre-push` | Git pre-push hook | Branch guard, then read-only push gate: tests, lint, format check, acceptance, arch over the whole tree | No |
| `ci` | CI pipeline | Read-only gates (lint, typecheck, dep audit, complexity, acceptance, arch) run in parallel, then coverage + advisory CRAP | No |
| `audit` | CI pipeline | Audit dependencies for known vulnerabilities | No |
| `post-edit` | Stop hook helper; `--hook` = PostToolUse | Fix and format changed source files (`--hook`: the one file just edited) | Yes |
| `stop-hook` | Agent Stop hook | Run `post-edit`, then gate only the change: lint left on changed lines, complexity new or worse than the merge-base (+ deadcode on changed lines where shipped). Silent on success; exit 2 with ≤20 `path:line` findings | Yes |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow. It also ratchets suppression comments (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`, etc.) against `.harness-baseline`: new suppressions fail unless a human signs off on `suppressions --update-baseline`.
**`pre-commit`** fixes, formats, and typechecks staged files, installed as a git hook. Tests moved to `pre-push`: agents commit often, and a commit on a feature branch is not an integration point.
**`pre-push`** is the read-only push gate — it first refuses direct pushes to or deletions of `main`/`master` (`branch-guard`, override with `HARNESS_ALLOW_PROTECTED_PUSH=1`), then runs the test suite and the read-only whole-tree gates: lint, format check, acceptance, arch (the checks `pre-commit` and `stop-hook` skip). Installed as a git pre-push hook.
For Go and Bun, the lint gate subsumes format checking.
**`ci`** is the read-only gate — no fixes, just verification. Its read-only gates run in parallel (captured, printed in submission order, run to completion), then coverage streams and CRAP runs advisory.
**`audit`** audits dependencies for known vulnerabilities.
**`post-edit`** runs the template's fixer on source files changed by an agent: format, plus the linter's auto-fix where the tool has one and can be scoped to those files (ruff, biome, golangci-lint `--fix` on the changed packages). `post-edit --hook` is the Claude PostToolUse handler: it fixes and formats the one file just edited and tells the agent to re-read it when it changed.
**`stop-hook`** is the Stop hook entrypoint. It runs `post-edit`, then gates only what the change introduced, measured against the merge-base with the default branch: lint the fixer left on changed lines, functions whose complexity is over the limit and new or worse, and dead code on changed lines (python/bun). Pre-existing debt never blocks an agent stop; `check` and `ci` still see the whole tree. It prints nothing on success, exits 2 with at most 20 `path:line` findings on stderr, exits 1 (never blocking) when a tool cannot run, and stops re-blocking on byte-identical findings. Contract: [settings-json.md](skills/harness/reference/settings-json.md).

## Available Templates

| Template | Stack | Prerequisites |
|---|---|---|
| [Python](python/) | uv, ruff, basedpyright, unittest | [uv](https://docs.astral.sh/uv/) |
| [Bun](bun/) | Bun, Biome, TypeScript | [Bun](https://bun.sh/) |
| [Go](go/) | Go, golangci-lint | [Go](https://go.dev/dl/) 1.24+, [golangci-lint](https://golangci-lint.run/welcome/install/) v2+ |
| [Rust](rust/) | Rust, clippy, rustfmt | [Rust](https://rustup.rs/) |
| [Monorepo](monorepo/) | Make dispatcher over any mix of the above | `make`, `bash`, `git` |

## Getting Started

### Python

```bash
cp -r python/ my-project && cd my-project
uv sync && uv run harness setup-hooks
# Start coding in src/
```

### Bun

```bash
cp -r bun/ my-project && cd my-project
bun install && bun run setup-hooks
# Start coding in src/
```

### Go

```bash
# Install golangci-lint if you don't have it
brew install golangci-lint  # or: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

cp -r go/ my-project && cd my-project
go mod edit -module my-project
go run harness.go setup-hooks
# Start coding
```

### Rust

```bash
cp -r rust/ my-project && cd my-project
cargo build && cargo harness setup-hooks
# Start coding in src/
```

### Monorepo

```bash
cp -r monorepo/ my-project && cd my-project
git init

# Drop in one or more single-language templates as subprojects:
cp -r ../harness-templates/python/ api
cp -r ../harness-templates/bun/    web

make bootstrap      # per-language install + root git hook
make check          # dispatches to every subproject
make check-api      # scope to one subproject
```

## What Each Template Includes

- **Single zero-dep task runner** (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`) — the source of truth, with a thin optional `Makefile` that just forwards to it (`make ci` == `harness ci`)
- **Linter + formatter** — ruff (Python) / Biome (Bun) / golangci-lint (Go) / clippy + rustfmt (Rust)
- **Type checker** — basedpyright (Python) / tsc (Bun) / Go compiler (Go) / Rust compiler (Rust)
- **Test runner** — unittest (Python) / bun test (Bun) / go test (Go) / cargo test (Rust)
- **Security scanning** — bandit rules via ruff (Python) / gosec via golangci-lint (Go) / clippy pedantic + `unsafe_code = "forbid"` (Rust)
- **Dependency auditing** — pip-audit (Python) / bun audit (Bun) / govulncheck (Go) / cargo-audit (Rust) — runs in `ci`
- **Cyclomatic complexity gate** (CCN 15, args 8) — lizard via `uvx` (Python/Bun/Go/Rust) / gocyclo via golangci-lint (Go) — runs in `ci`
- **Dead-code detection** — vulture (Python, via `uvx`) / knip (Bun, via `bunx`); Go & Rust use their linters (golangci-lint `unused` / clippy `dead_code`) — runs in `ci`; `stop-hook` reports findings on changed lines
- **CRAP advisory** — complexity × coverage signal, advisory by default and still run in `ci`
- **Suppression baseline ratchet** — `.harness-baseline` tracks allowed suppression counts and the coverage floor (`coverage.min`)
- **Arch config guard** — protected architecture config changes warn in `check` / `pre-commit` and fail `pre-push` / `ci` unless reviewed with `HARNESS_ALLOW_ARCH_CONFIG=1`
- **Branch guard** — `pre-push` refuses direct pushes to `main`/`master` so agents work on feature branches and open PRs; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides. It catches accidents, not `--no-verify`; merge ownership stays with the human by rule or server-side branch protection
- **Agent hooks** — `.claude/settings.json` runs `stop-hook` on Stop and `post-edit --hook` on PostToolUse (`Edit|Write`); `.codex/hooks.json` runs the Codex JSON wrapper around `stop-hook`
- **Property-based testing** — hypothesis (Python) / fast-check (Bun) / rapid (Go) / proptest (Rust), seeded with a property suite over each template's own CRAP and parser helpers as the worked example; runs under the normal `test` step
- **AGENTS.md + CLAUDE.md** — tell AI agents which commands to run and when, plus the behavior contract: plan first, commit on a branch and open a PR, specify what is worth specifying with a `.feature`, arch config changes in their own commit

## Design Principles

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything; agent hooks print nothing on success
- **Gate the change at agent stop** — `stop-hook` blocks only on what the change introduced; the whole tree is `check`/`pre-push`/`ci`'s job
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only
- **Tools own everything checkable** — formatting, lint, types, dead code, drift, and complexity are decided by deterministic tools and auto-fixed where the tool can. The agent reads the output and fixes the code, never the gate
- **Quality gates are hard, permission gates are two** — lint, types, arch boundaries, complexity, suppression ratchet, dead code, dependency audit, and drift block. Only `arch-config-guard` (pre-push/ci) and `branch-guard` (pre-push) need a human to unblock; everything else an agent can clear by doing the work
- **Metrics that can be gamed are advisory** — CRAP and mutation point at the next test or split; they are never gates, because a coverage-shaped target gets satisfied with assertion-free tests. The coverage floor is a ratchet from `.harness-baseline`, raised by a human, never a target number

## Harness skill

The skill that bootstraps repos to match these templates lives in
`skills/harness/`. Edit there; run `make sync-skills` to deploy to
`~/.claude/skills/harness/` and `~/.agents/skills/harness/`. `make
skills-drift` (run by `make check`) fails if the deployed copies have
diverged.

This repo root dogfoods the same hook shape as `monorepo/`: `.claude/settings.json`
runs `make -s stop-hook` and `make -s post-edit-hook`, and `.codex/hooks.json` runs
the Codex JSON wrapper around `make -s stop-hook`. Root `make check` verifies skill drift, root
`AGENTS.md`/`CLAUDE.md` drift, and protected arch-config changes.

## License

[MIT](LICENSE)
