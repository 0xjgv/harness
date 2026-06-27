# Go Template

Opinionated Go project template with built-in quality guardrails: linting, formatting, complexity gating, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Stack

- **Runner**: `go run harness.go` — zero-dep task runner (stdlib only)
- **Linter + Formatter**: golangci-lint v2 (with gofmt + goimports)
- **Test runner**: `go test`
- **Acceptance**: [godog](https://github.com/cucumber/godog) (Gherkin, run as a `go test`)
- **Architecture**: [go-arch-lint](https://github.com/fe3dback/go-arch-lint) (dependency-boundary linter)
- **Complexity**: [lizard](https://github.com/terryyin/lizard) (fetched on demand via `uvx`)
- **Mutation**: gremlins (fetched on demand via `go run`)

## Prerequisites

- [Go](https://go.dev/dl/) 1.24+
- [golangci-lint](https://golangci-lint.run/welcome/install/) v2+
- [uv](https://docs.astral.sh/uv/) on `PATH` — `uvx` runs `lizard@1.22.2` for the complexity and CRAP gates

Everything else (godog, go-arch-lint, gremlins, govulncheck) is pulled on
demand by `go run ...@version`; lizard is pulled on demand by `uvx`. No
separate install step for any of them.

## Getting Started

```bash
cp -r go/ my-project && cd my-project
go mod edit -module my-project
go run harness.go setup-hooks
# Start coding
```

## The 5-Script Contract

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `go run harness.go check` | After edits | Fix, format, lint, test | Yes |
| `go run harness.go pre-commit` | Git hook | Staged files only | Yes |
| `go run harness.go pre-push` | Git pre-push hook | Read-only push gate: lint, acceptance, arch over the whole tree | No |
| `go run harness.go ci` | CI pipeline | Read-only verification (see below) | No |
| `go run harness.go audit` | CI pipeline | Dependency vulnerability audit | No |
| `go run harness.go post-edit` | Stop hook helper | Format if source files changed | No |
| `go run harness.go stop-hook` | Stop hook entrypoint | Format/fix changed files, then run complexity and advisory CRAP | Yes |

### `ci` pipeline

`harness ci` runs the read-only gates — lint, dep audit, complexity (lizard, CCN 15,
args 8), acceptance (godog), arch (go-arch-lint) — **in parallel**: each is captured
and printed in submission order, and the batch runs to completion so one pass surfaces
every failure. It then streams coverage (`go test -race -coverprofile`) and the
advisory CRAP.

`pre-push` is the offline push gate — lint (golangci-lint covers format), acceptance,
arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

Dead code needs no separate gate — golangci-lint's `unused` linter (run by `lint`)
already flags unreachable functions, vars, and types. (`x/tools/cmd/deadcode` only
analyzes programs with a `main` package, not this library template.)

CRAP is advisory: it warns by default and exits 0 unless `--enforce` is passed.
Mutation testing is also advisory and is NOT wired into `ci` — invoke explicitly.

### Continuous integration

`.github/workflows/ci.yml` runs `go run harness.go ci` on every push to `main`
and every pull request — the same gate you run locally, so local gate == remote
gate. It installs golangci-lint and `uv` (for the lizard gates). Copying the
template into a repo brings CI along.

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
| `go run harness.go complexity` | Cyclomatic complexity gate (lizard, CCN 15, args 8; excludes `_test.go` + `harness.go`) |
| `go run harness.go acceptance` | Run acceptance scenarios (godog) against `features/` |
| `go run harness.go arch` | Architecture checks (go-arch-lint) |
| `go run harness.go mutation` | Mutation testing (gremlins, advisory) |
| `go run harness.go crap` | CRAP complexity × coverage gate (advisory) |
| `go run harness.go pre-commit` | Staged checks + tests |
| `go run harness.go pre-push` | Read-only push gate: lint, acceptance, arch |
| `go run harness.go ci` | Full verification pipeline |
| `go run harness.go setup-hooks` | Install git pre-commit + pre-push hooks and the Claude/Codex Stop wiring |
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

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Claude Code hooks enforce it for Claude; agents that read `AGENTS.md` receive the same instructions.

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: `git commit` / `git push` denied unless the user's current prompt explicitly asked (verbs: `commit`, `push`, `ship`, `land`, `merge`).
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Config write-protection**: edits to `.go-arch-lint.yml` denied unless the user names the path in their prompt. Note `.golangci.yaml` is deliberately *not* protected — it is the general lint config, and protecting it would block all lint-config edits.

Hook scripts live in `.claude/scripts/`. Stop hooks are wired via
`.claude/settings.json` for Claude and `.codex/hooks.json` for Codex.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- Complexity is gated at CCN 15 and args 8 (lizard + golangci-lint's `gocyclo`); lower it once the codebase is clean.
- Acceptance ships one smoke `.feature`; an empty `features/` dir warns and passes. Add real scenarios.
- Mutation / CRAP are advisory — enable as blocking gates once baselines are established.
- `crap --max=30` is the starting ceiling; tighten it as coverage rises.
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
