# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The 5-Script Contract

Every template implements exactly 5 scripts:

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test | Yes |
| `pre-commit` | Git hook | Staged files only — fix, format, typecheck, test if source changed | Yes |
| `ci` | CI pipeline | Read-only lint, typecheck, dep audit, tests with coverage | No |
| `audit` | CI pipeline | Audit dependencies for known vulnerabilities | No |
| `post-edit` | Claude Code hook | Format if source files changed | No |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow. It also reports suppression comments (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`, etc.) as a report-only signal — visibility, never exit-code change.
**`pre-commit`** runs the same checks scoped to staged files, installed as a git hook.
**`ci`** is the read-only gate — no fixes, just verification.
**`audit`** audits dependencies for known vulnerabilities.
**`post-edit`** formats source files if changed by Claude Code.

## Available Templates

| Template | Stack | Prerequisites |
|---|---|---|
| [Python](python/) | uv, ruff, basedpyright, pytest | [uv](https://docs.astral.sh/uv/) |
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
- **Test runner** — pytest (Python) / bun test (Bun) / go test (Go) / cargo test (Rust)
- **Security scanning** — bandit rules via ruff (Python) / gosec via golangci-lint (Go) / clippy pedantic + `unsafe_code = "forbid"` (Rust)
- **Dependency auditing** — pip-audit (Python) / bun audit (Bun) / govulncheck (Go) / cargo-audit (Rust) — runs in `ci`
- **CLAUDE.md** — tells AI agents which commands to run and when

## Design Principles

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only

## License

[MIT](LICENSE)
