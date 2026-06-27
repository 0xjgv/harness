# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The 5-Script Contract

Every template implements the 5 required scripts plus a `stop-hook` target
used by the Stop hook:

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test | Yes |
| `pre-commit` | Git pre-commit hook | Staged files only — fix, format, typecheck, test if source changed | Yes |
| `pre-push` | Git pre-push hook | Read-only push gate: lint, format check, acceptance, arch over the whole tree, in parallel | No |
| `ci` | CI pipeline | Read-only gates (lint, typecheck, dep audit, complexity, acceptance, arch) run in parallel, then coverage + advisory CRAP | No |
| `audit` | CI pipeline | Audit dependencies for known vulnerabilities | No |
| `post-edit` | Stop hook helper | Format if source files changed | Yes |
| `stop-hook` | Agent Stop hook | Run `post-edit`, complexity, advisory CRAP | Yes |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow. It also reports suppression comments (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`, etc.) as a report-only signal — visibility, never exit-code change.
**`pre-commit`** runs the same checks scoped to staged files, installed as a git hook.
**`pre-push`** is the read-only push gate — lint, format check, acceptance, arch over the whole pushed tree (the offline checks `pre-commit` and `stop-hook` skip), run in parallel. Installed as a git pre-push hook.
**`ci`** is the read-only gate — no fixes, just verification. Its read-only gates run in parallel (captured, printed in submission order, run to completion), then coverage streams and CRAP runs advisory.
**`audit`** audits dependencies for known vulnerabilities.
**`post-edit`** formats source files if changed by an agent.
**`stop-hook`** is the Stop hook entrypoint: it runs `post-edit`, then complexity and advisory CRAP.

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

- **Single zero-dep task runner** (`harness.py` / `harness.ts` / `harness.go`) — no Makefile, no task framework
- **Linter + formatter** — ruff (Python) / Biome (Bun) / golangci-lint (Go) / clippy + rustfmt (Rust)
- **Type checker** — basedpyright (Python) / tsc (Bun) / Go compiler (Go) / Rust compiler (Rust)
- **Test runner** — unittest (Python) / bun test (Bun) / go test (Go) / cargo test (Rust)
- **Security scanning** — bandit rules via ruff (Python) / gosec via golangci-lint (Go) / clippy pedantic + `unsafe_code = "forbid"` (Rust)
- **Dependency auditing** — pip-audit (Python) / bun audit (Bun) / govulncheck (Go) / cargo-audit (Rust) — runs in `ci`
- **Cyclomatic complexity gate** (CCN 15, args 8) — lizard via `uvx` (Python/Bun/Go/Rust) / gocyclo via golangci-lint (Go) — runs in `ci`
- **Dead-code detection** — vulture (Python, via `uvx`) / knip (Bun, via `bunx`); Go & Rust use their linters (golangci-lint `unused` / clippy `dead_code`) — runs in `ci` + `stop-hook`
- **CRAP advisory** — complexity × coverage signal, advisory by default and still run in `ci`
- **Agent Stop hooks** — `.claude/settings.json` runs `stop-hook`; `.codex/hooks.json` runs the Codex JSON wrapper around `stop-hook`
- **Property-based testing** — hypothesis (Python) / fast-check (Bun) / rapid (Go) / proptest (Rust), seeded with a property suite over each template's own CRAP and parser helpers as the worked example; runs under the normal `test` step
- **AGENTS.md + CLAUDE.md** — tell AI agents which commands to run and when

## Design Principles

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only

## Harness skill

The skill that bootstraps repos to match these templates lives in
`skills/harness/`. Edit there; run `make sync-skills` to deploy to
`~/.claude/skills/harness/` and `~/.agents/skills/harness/`. `make
skills-drift` (run by `make check`) fails if the deployed copies have
diverged.

## License

[MIT](LICENSE)
