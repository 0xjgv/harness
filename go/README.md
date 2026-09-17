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
| `go run harness.go check` | After edits | Fix, format, lint, test, suppression ratchet | Yes |
| `go run harness.go pre-commit` | Git hook | Fix/format staged packages; mirrors a staged `CLAUDE.md` into `AGENTS.md` | Yes |
| `go run harness.go pre-push` | Git pre-push hook | Branch guard, tests, then a read-only push gate: lint, agents-md drift, acceptance, arch over the whole tree | No |
| `go run harness.go ci` | CI pipeline | Read-only verification (see below) | No |
| `go run harness.go audit` | CI pipeline | Dependency vulnerability audit | No |
| `go run harness.go post-edit` | Stop hook helper; `--hook` is the Claude PostToolUse hook | Format changed files, fix their packages; `--hook`: the one edited file, never blocks | Yes |
| `go run harness.go stop-hook` | Stop hook entrypoint | post-edit, then changed-lines lint, complexity delta; silent on success, exit 2 with findings | Yes |

### `ci` pipeline

`harness ci` runs the read-only gates — lint, dep audit, complexity (lizard, CCN 15,
args 8), agents-md drift, acceptance (godog), arch (go-arch-lint) — **in parallel**:
each is captured and printed in submission order, and the batch runs to completion so
one pass surfaces every failure. It then streams coverage (`go test -race -coverprofile`,
default threshold from `.harness-baseline`) and the
advisory CRAP.

`pre-push` is the offline push gate — the branch guard (no pushes to `main`/`master`),
then the test suite (run alone and without git's `GIT_*` hook variables, since the tests
build a binary and create temp git repos), then lint (golangci-lint covers format),
agents-md drift, acceptance, arch over the whole pushed tree (the deterministic checks
pre-commit and stop-hook skip). `pre-commit` no longer runs tests.

Dead code needs no separate gate — golangci-lint's `unused` linter (run by `lint`)
already flags unreachable functions, vars, and types, and `go mod tidy` prunes
unused dependencies. (`x/tools/cmd/deadcode` only analyzes programs with a `main`
package, not this library template.)

The gates split into four groups: hard quality gates that block (lint, arch,
complexity, suppressions, dead code via lint's `unused`, audit, agents-md drift),
advisory metrics that inform but never fail the build (CRAP, mutation), a ratchet
that only moves up (the coverage floor in `.harness-baseline`), and two permission
gates that need a human to unblock (arch-config-guard, branch-guard). CRAP is
**advisory**: it warns by default and exits 0 unless `--enforce` is passed. Mutation
testing is also advisory and is NOT wired into `ci` — invoke explicitly.

### Continuous integration

`.github/workflows/ci.yml` runs `go run harness.go ci` on every push to `main`
and every pull request — the same gate you run locally, so local gate == remote
gate. It installs golangci-lint and `uv` (for the lizard gates). Copying the
template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
go run harness.go check --verbose
```

Every command is also a `make` target — `make check`, `make ci`, `make pre-push`, and so on forward to the harness. `make bootstrap` does first-time setup (`go mod download` + `setup-hooks`) in one step.

## All Commands

| Command | Description |
|---|---|
| `go run harness.go check` | Full pre-flight: fix + format + lint + test |
| `go run harness.go fix` | Fix lint errors + format code |
| `go run harness.go lint` | Lint + format check (read-only) |
| `go run harness.go test` | Run tests |
| `go run harness.go coverage` / `test-cov` | Run tests with race detector + coverage |
| `go run harness.go audit` | Audit dependencies for known vulnerabilities |
| `go run harness.go complexity` | Cyclomatic complexity gate (lizard, CCN 15, args 8; excludes `_test.go` + `harness.go`) |
| `go run harness.go acceptance` | Run acceptance scenarios (godog) against `features/` |
| `go run harness.go arch` | Architecture checks (go-arch-lint) |
| `go run harness.go mutation` | Mutation testing (gremlins, advisory) |
| `go run harness.go crap` | CRAP complexity × coverage gate (advisory) |
| `go run harness.go suppressions` | Suppression breakdown; `--update-baseline` with human sign-off |
| `go run harness.go pre-commit` | Staged fix/format; mirrors a staged `CLAUDE.md` |
| `go run harness.go pre-push` | Push gate: branch guard, tests, lint, agents-md drift, acceptance, arch |
| `go run harness.go branch-guard` | Refuse pushes to main/master |
| `go run harness.go ci` | Full verification pipeline |
| `go run harness.go setup-hooks` | Install git pre-commit + pre-push hooks, the Claude/Codex Stop wiring, and the Claude PostToolUse wiring |
| `go run harness.go stop-hook` | post-edit, then changed-lines lint, complexity delta; silent on success, exit 2 with findings |
| `go run harness.go post-edit` | Format changed files and fix their packages (`--hook`: the file a PostToolUse names) |
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
```

## Behavior contract

`AGENTS.md` and `CLAUDE.md` encode the same AI behavior contract. Agents that read either file receive the same instructions.

- **Plan first**: open with the sub-tasks and files, then execute in the same turn.
- **Human-is-engineer**: commit and push on a feature branch; never `main`/`master`, never force-push, never merge.
- **Specify what is worth specifying**: `.feature` scenarios for user-visible flows, law-like rules, and cross-component contracts; unit tests suffice for the rest.
- **Branch guard**: `pre-push` refuses a push that lands on (or deletes) `main`/`master` unless `HARNESS_ALLOW_PROTECTED_PUSH=1` is set. Destinations come from `HARNESS_PRE_PUSH_REFS`, else the git pre-push stdin refs (1s deadline; partial input fails), else the current branch. It stops accidents, not `--no-verify` — agents push feature branches, humans merge.
- **Arch config guard**: `.go-arch-lint.yml` changes warn during `check`/`pre-commit` and fail `pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Note `.golangci.yaml` is deliberately *not* protected — it is the general lint config, and protecting it would block all lint-config edits.

Stop hooks are wired via `.claude/settings.json` for Claude and
`.codex/hooks.json` for Codex; Claude also gets a PostToolUse hook
(`post-edit --hook`, Edit|Write) that formats the edited file. The Stop commands
run `go build -o harness harness.go && ./harness stop-hook` (the `harness` binary is
gitignored): `go run` reports any non-zero exit as 1, which would turn the blocking
exit 2 into a non-blocking error.

`stop-hook` judges the change, not the tree. Its scope is `git diff` against the
merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then
origin/HEAD, origin/main, origin/master, main, master; never fetched) plus untracked
files. It first formats the changed files (`golangci-lint fmt`) and applies lint fixes in
their packages only (`golangci-lint run --fix --new-from-rev=HEAD`). It blocks (exit 2,
findings on stderr, at most 20 lines) on lint left on changed lines
(`golangci-lint run --new-from-rev=<merge-base>` over the changed packages, which
includes `unused` for dead code; `gocyclo` is left to the complexity delta, which
matches functions by signature; compile errors block wherever they sit) and on a
non-test function that is over a lizard limit and new or worse than at the base.
Pre-existing debt never blocks. It prints nothing on success. Exit 1 means a tool could
not run, or the same findings came back while the agent was already continuing from a
stop (the loop guard, state under `git rev-parse --git-path harness`). An uncommitted
`CLAUDE.md` edit is copied to `AGENTS.md`.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- Complexity is gated at CCN 15 and args 8 (lizard + golangci-lint's `gocyclo`); lower it once the codebase is clean.
- Acceptance ships one smoke `.feature`; an empty `features/` dir warns and passes. Add real scenarios.
- `coverage` — floor from the baseline; `--min=N` overrides it locally, it is not a target.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `harness suppressions --update-baseline` only with human sign-off.
- CRAP and mutation are advisory by design — they tell you where the next test or split pays off, they are not gates, because a coverage-shaped target gets satisfied with assertion-free tests. `--enforce` exists for teams that want it on CRAP; it is not the recommended default.
- The coverage floor is a ratchet: `.harness-baseline` `coverage.min` only ever moves up, by a human, and starts at 0.
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
