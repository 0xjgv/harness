# Go Template

Opinionated Go project template with built-in quality guardrails: linting, formatting, complexity gating, acceptance scenarios, coverage, mutation/CRAP advisories, and architecture checks.

## Stack

- **Runner**: `make <target>` enters the managed Go profile and runs the zero-dependency task runner
- **Linter + Formatter**: managed golangci-lint 2.12.2 (with gofmt + goimports)
- **Test runner**: `go test`
- **Acceptance**: [godog](https://github.com/cucumber/godog) (Gherkin, run as a `go test`)
- **Architecture**: managed go-arch-lint 1.15.0 (dependency-boundary linter)
- **Complexity**: managed lizard 1.22.2
- **Audit**: managed govulncheck 1.1.4
- **Mutation**: managed gremlins 0.5.0

The provisioner also supplies Go 1.27.0. No ambient Go, golangci-lint, uv, or
analyzer installation is part of the project contract.

## Getting Started

```bash
cp -r go/ my-project && cd my-project
go mod edit -module my-project
git init
git add . && git commit -m "Initial Go template"
make workspace
# Start coding
```

The module edit is the template customization and must happen before the clean
initial commit. The [root workspace contract](../README.md#autonomous-workspace)
lists the supported macOS/glibc Linux platforms and the small VM bootstrap
layer: Make, Bash, Git, curl, tar, Info-ZIP unzip, a SHA-256 utility, and a
writable `HOME`. `make workspace` installs the exact managed tools and locked
modules, deploys skills, installs Git hooks, verifies the checked-in Stop
wiring, and runs `make check`. It requires a clean tracked/index state;
untracked files are preserved.

After an online run has populated the exact tool and dependency caches, the
same workspace converges without network access:

```bash
make workspace OFFLINE=1
```

Offline mode makes no network requests and fails on a cold or incomplete
cache. `make bootstrap` is a compatibility alias for `make workspace`.

### Dependencies and upgrades

`make deps` restores committed modules with
`GOFLAGS=-mod=readonly go mod download`. In offline mode, the provisioner also
sets `GOPROXY=off`. Neither path rewrites `go.mod` or `go.sum`.

Dependency upgrades are explicit. Run native Go operations through the managed
boundary, then review both lock inputs before committing them:

```bash
.harness/workspace.sh exec go -- go get -u ./...
.harness/workspace.sh exec go -- go mod tidy
git diff -- go.mod go.sum
make deps
```

## The 5-Script Contract

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `make check` | After edits | Fix, format, lint, test, suppression ratchet | Yes |
| `make pre-commit` | Git hook | Staged files only | Yes |
| `make pre-push` | Git pre-push hook | Read-only push gate: lint, acceptance, arch over the whole tree | No |
| `make ci` | CI pipeline | Read-only verification (see below) | No |
| `make audit` | CI pipeline | Dependency vulnerability audit | No |
| `make post-edit` | Stop hook helper | Format if source files changed | Yes |
| `make stop-hook` | Stop hook entrypoint | Format/fix changed files, then run complexity | Yes |

### `ci` pipeline

`make ci` runs the read-only gates — lint, dep audit, complexity (lizard, CCN 15,
args 8), acceptance (godog), arch (go-arch-lint) — **in parallel**: each is captured
and printed in submission order, and the batch runs to completion so one pass surfaces
every failure. It then streams coverage (`go test -race -coverprofile`, default
threshold from `.harness-baseline`) and the
advisory CRAP.

`pre-push` is the offline push gate — lint (golangci-lint covers format), acceptance,
arch over the whole pushed tree (the deterministic checks pre-commit and stop-hook skip).

Dead code needs no separate gate — golangci-lint's `unused` linter (run by `lint`)
already flags unreachable functions, vars, and types, and `go mod tidy` prunes
unused dependencies. (`x/tools/cmd/deadcode` only analyzes programs with a `main`
package, not this library template.)

CRAP is advisory: it warns by default and exits 0 unless `--enforce` is passed.
Mutation testing is also advisory and is NOT wired into `ci` — invoke explicitly.

### Continuous integration

`.github/workflows/ci.yml` runs the checked-in contract on every push to `main`
and every pull request:

```bash
make workspace
make ci
```

The local and remote gates use the same managed tools and locks. The workflow
ships with the template, so copying the template into a repo brings CI along.

All commands minimize output — only errors are shown. Add `--verbose` for full output:

```bash
.harness/workspace.sh exec go -- go run -mod=readonly harness.go check --verbose
```

Make enters the checked-in managed environment for every normal harness target.
Use the full provisioner boundary when passing harness-specific arguments.

## All Commands

| Command | Description |
|---|---|
| `make check` | Full pre-flight: fix + format + lint + test |
| `make fix` | Fix lint errors + format code |
| `make lint` | Lint + format check (read-only) |
| `make test` | Run tests |
| `make test-cov` | Run tests with race detector + coverage |
| `make audit` | Audit dependencies for known vulnerabilities |
| `make complexity` | Cyclomatic complexity gate (lizard, CCN 15, args 8; excludes `_test.go` + `harness.go`) |
| `make acceptance` | Run acceptance scenarios (godog) against `features/` |
| `make arch` | Architecture checks (go-arch-lint) |
| `make mutation` | Mutation testing (gremlins, advisory) |
| `make crap` | CRAP complexity × coverage gate (advisory) |
| `make pre-commit` | Staged checks + tests |
| `make pre-push` | Read-only push gate: lint, acceptance, arch |
| `make ci` | Full verification pipeline |
| `make clean` | Remove coverage and test cache |

Advanced arguments use the full managed runner form:

```bash
.harness/workspace.sh exec go -- go run -mod=readonly harness.go test-cov --min=80
.harness/workspace.sh exec go -- go run -mod=readonly harness.go crap --max=30
.harness/workspace.sh exec go -- go run -mod=readonly harness.go suppressions --update-baseline
```

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

- **Task sizing**: max 5 sub-tasks, each ≤1 non-test file + ≤1 test.
- **Human-is-engineer**: do not `git commit` / `git push` unless the user's current prompt explicitly asked.
- **Gherkin-first** for user-visible behavior changes (refactors / typos / dep bumps exempted if declared).
- **Arch config guard**: `.go-arch-lint.yml` changes warn during `check`/`stop-hook` and fail `pre-commit`/`pre-push`/`ci` unless `HARNESS_ALLOW_ARCH_CONFIG=1` is set after review. Note `.golangci.yaml` is deliberately *not* protected — it is the general lint config, and protecting it would block all lint-config edits.

`make workspace` installs the Git hooks and verifies the checked-in Claude and
Codex Stop configuration. Every Git and Stop hook enters the Git root and calls
`make pre-commit`, `make pre-push`, or `make stop-hook`, so Make supplies the
exact managed environment. Unknown existing Git hooks cause setup to fail
without modification. Only exact legacy harness shims are migrated.

## Thresholds: start at 0, ratchet up

Day-1 defaults are deliberately loose so adopting this template does not fail existing projects:

- Complexity is gated at CCN 15 and args 8 (lizard + golangci-lint's `gocyclo`); lower it once the codebase is clean.
- Acceptance ships one smoke `.feature`; an empty `features/` dir warns and passes. Add real scenarios.
- `coverage --min=0` — explicit flags win; otherwise the default comes from `.harness-baseline` `coverage.min`.
- `.harness-baseline` also ratchets suppression counts. New suppressions fail `check`; run `.harness/workspace.sh exec go -- go run -mod=readonly harness.go suppressions --update-baseline` only with human sign-off.
- Mutation / CRAP are advisory — enable as blocking gates once baselines are established.
- `.harness/workspace.sh exec go -- go run -mod=readonly harness.go crap --max=30` is the starting ceiling; tighten it as coverage rises.
- `.go-arch-lint.yml` ships with one starter rule (the sample `suppressions` package is a leaf — it may not import other project components). Extend the component graph as the module grows.

### Mutation testing notes

`make mutation` runs [gremlins](https://github.com/go-gremlins/gremlins) and is advisory:

- It warms the Go build cache (`go test -count=1`) before running. gremlins derives each
  mutant's test timeout from the baseline run; a cold cache makes the first mutant compile
  blow that budget and every mutant reports `TIMED OUT`.
- gremlins must target a concrete package. `./...` gathers no coverage here because the
  module root (`harness.go`) is `//go:build ignore`. The command targets `./suppressions`
  by default — pass a path through the managed runner to mutate a different package:
  `.harness/workspace.sh exec go -- go run -mod=readonly harness.go mutation ./mypkg`.

## Starting from This Template

```bash
cp -r go/ my-project && cd my-project
go mod edit -module my-project
git init
git add . && git commit -m "Initial Go template"
make workspace
```

Replace `suppressions/` with your own packages, update `.go-arch-lint.yml` to
match the module layout, and add real scenarios under `features/` before writing
user-visible behavior.
