# Go Template

Opinionated Go project template with built-in quality guardrails.

## Stack

- **Runner**: `go run harness.go` — zero-dep task runner (stdlib only)
- **Linter + Formatter**: golangci-lint v2 (with gofmt + goimports)
- **Test runner**: `go test`

## Prerequisites

- [Go](https://go.dev/dl/) 1.24+
- [golangci-lint](https://golangci-lint.run/welcome/install/) v2+

## Getting Started

```bash
cp -r go/ my-project && cd my-project
go mod edit -module my-project
go run harness.go setup-hooks
# Start coding
```

## The 3-Script Contract

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `go run harness.go check` | After edits | Fix, format, lint, test | Yes |
| `go run harness.go pre-commit` | Git hook | Staged files only | Yes |
| `go run harness.go ci` | CI pipeline | Read-only lint, tests with race + coverage | No |

## All Commands

| Command | Description |
|---|---|
| `go run harness.go check` | Full pre-flight: fix + format + lint + test |
| `go run harness.go fix` | Fix lint errors + format code |
| `go run harness.go lint` | Lint + format check (read-only) |
| `go run harness.go test` | Run tests |
| `go run harness.go pre-commit` | Staged checks + tests |
| `go run harness.go ci` | Lint + tests with race detector and coverage |
| `go run harness.go setup-hooks` | Install git pre-commit hook |
| `go run harness.go clean` | Remove coverage and test cache |

Add `--verbose` to any command to see all output.
