# Go Template

Opinionated Go project template with built-in quality guardrails: linting, formatting, complexity gating, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Stack

- **Runner**: `go run harness.go` — zero-dep task runner (stdlib only)
- **Linter + Formatter**: golangci-lint v2 (with gofmt + goimports)
- **Test runner**: `go test`
- **Acceptance**: [godog](https://github.com/cucumber/godog) (Gherkin, run as a `go test`)
- **Architecture**: [go-arch-lint](https://github.com/fe3dback/go-arch-lint) (dependency-boundary linter)
- **Complexity / Mutation**: gocyclo, gremlins (fetched on demand via `go run`)

## Prerequisites

- [Go](https://go.dev/dl/) 1.24+
- [golangci-lint](https://golangci-lint.run/welcome/install/) v2+

Everything else (godog, go-arch-lint, gocyclo, gremlins, govulncheck) is pulled
on demand by `go run ...@version` — no separate install step.

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
| `go run harness.go ci` | CI pipeline | Read-only verification (see below) | No |

### `ci` pipeline

`harness ci` runs, in order: lint → dep audit → complexity (gocyclo, CCN 15) →
acceptance (godog) → coverage (`go test -race -coverprofile`) → arch (go-arch-lint).

Mutation testing and CRAP are **advisory**: not wired into `ci`; invoke explicitly.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
go run harness.go check --verbose
```

## All Commands

| Command | Description |
|---|---|
| `go run harness.go check` | Full pre-flight: fix + format + lint + test |
| `go run harness.go fix` | Fix lint errors + format code |
| `go run harness.go lint` | Lint + format check (read-only) |
| `go run harness.go test` | Run tests |
| `go run harness.go test-cov` | Run tests with race detector + coverage |
| `go run harness.go audit` | Audit dependencies for known vulnerabilities |
| `go run harness.go complexity` | Cyclomatic complexity gate (gocyclo, CCN 15) |
| `go run harness.go acceptance` | Run acceptance scenarios (godog) against `features/` |
| `go run harness.go arch` | Architecture checks (go-arch-lint) |
| `go run harness.go mutation` | Mutation testing (gremlins, advisory) |
| `go run harness.go crap` | CRAP complexity × coverage gate (advisory) |
| `go run harness.go pre-commit` | Staged checks + tests |
| `go run harness.go ci` | Full verification pipeline |
| `go run harness.go setup-hooks` | Install git pre-commit hook |
| `go run harness.go clean` | Remove coverage and test cache |

Add `--verbose` to any command to see all output.

## Project Structure

```bash
suppressions/        Sample library package (replace with your own)
features/            Gherkin scenarios (godog) + acceptance_test.go runner
features/steps/      Step definitions
harness.go           Development task runner (zero dependencies; //go:build ignore)
.go-arch-lint.yml    Architecture rules (go-arch-lint)
.golangci.yaml       Lint + format config (golangci-lint v2)
.claude/scripts/     Hook scripts (session reinject, commit-intent classifier, pre-tool gates)
```

## Behavior contract

`CLAUDE.md` encodes an AI behavior contract enforced by hooks:

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: `git commit` / `git push` denied unless the user's current prompt explicitly asked (verbs: `commit`, `push`, `ship`, `land`, `merge`).
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Config write-protection**: edits to `.go-arch-lint.yml` denied unless the user names the path in their prompt. Note `.golangci.yaml` is deliberately *not* protected — it is the general lint config, and protecting it would block all lint-config edits.

Hook scripts live in `.claude/scripts/` and are wired via `.claude/settings.json`.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- Complexity is gated at CCN 15 (gocyclo + golangci-lint's `gocyclo`); lower it once the codebase is clean.
- Acceptance ships one smoke `.feature`; an empty `features/` dir warns and passes. Add real scenarios.
- Mutation / CRAP are advisory — enable as blocking gates once baselines are established.
- `crap --max=30` is the starting ceiling; tighten it as coverage rises. `crap --changed-only` limits the check to files changed vs `origin/main`.
- `.go-arch-lint.yml` ships with one starter rule (the sample `suppressions` package is a leaf — it may not import other project components). Extend the component graph as the module grows.

### Mutation testing notes

`harness mutation` runs [gremlins](https://github.com/go-gremlins/gremlins) and is advisory:

- It warms the Go build cache (`go test -count=1`) before running. gremlins derives each
  mutant's test timeout from the baseline run; a cold cache makes the first mutant compile
  blow that budget and every mutant reports `TIMED OUT`.
- gremlins must target a concrete package. `./...` gathers no coverage here because the
  module root (`harness.go`) is `//go:build ignore`. The command targets `./suppressions`
  by default — pass a path to mutate a different package: `harness mutation ./mypkg`.

## Starting from This Template

1. Copy this directory
2. `go mod edit -module my-project`
3. `go run harness.go setup-hooks`
4. Replace `suppressions/` with your own packages
5. Update `.go-arch-lint.yml` components to match your module layout
6. Add real scenarios under `features/` before writing user-visible behavior
