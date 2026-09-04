# go

Source: `~/Code/harness-templates/go/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/go/CLAUDE.md` verbatim; do not
paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `pre-push`, `ci`, `audit`, plus
  quality subcommands `complexity`, `acceptance`, `coverage` (`test-cov` alias), `mutation`,
  `crap`, `arch`, `arch-config-guard`, `gherkin-guard`, `suppressions`, and the
  drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift). `check` runs fix + format + lint (`--fix`), test — scoped to the
  packages the change touches (one `go test ./<dir>/...` per changed `.go`
  file's directory; an empty scope warns and skips instead of widening, a
  changed package with no `*_test.go` warns instead of failing, `--all` runs
  the whole suite, and `--base=<ref>`/`HARNESS_ARCH_BASE`/`GITHUB_BASE_REF`
  add a base ref's diff to that scope; `pre-commit` scopes to the staged
  packages, and `ci` still runs the whole suite under coverage) — then, as
  a read-only parallel batch, complexity, `go mod tidy -diff` (fails if
  `go.mod`/`go.sum` don't match what `go mod tidy` would produce), and
  acceptance (self-skips with a warning when no `.feature` files exist), then
  warns (does not block) via `arch-config-guard` and `gherkin-guard`, checks
  `agents-md-drift`, and ratchets suppressions. Invariant: `ci` minus `check`
  == every gate that needs the network or a build lock (`audit`, `coverage`,
  advisory `crap`) plus the architecture boundary check itself (`arch`, which
  stays `ci`/`pre-push`-only to keep this edit-triggered local loop fast — it
  needs to fetch a module). `ci` runs the read-only gates (`lint`, `audit`,
  `complexity`, `go mod tidy -diff`, `acceptance`, `arch`) **in parallel** — captured and
  printed in submission order, run to completion so one pass surfaces every
  failure — then streams `coverage` and the advisory `crap`; `ci` also runs
  `arch-config-guard` and `gherkin-guard` in strict (blocking) mode. `pre-push` is
  the offline push gate: `lint` (golangci-lint covers format), `acceptance`,
  `arch`, and strict `arch-config-guard` + `gherkin-guard` over the whole pushed tree (the
  deterministic checks pre-commit and stop-hook skip). There is **no** `deadcode` target — golangci-lint's
  `unused` linter (run by the `lint` gate) already flags unreachable
  functions, vars, and types, and `go mod tidy` prunes unused dependencies;
  `x/tools/cmd/deadcode` only works on programs with a `main` package, not
  this library template. `gherkin-guard` blocks a changed non-test `.go` file
  outside `features/` (excluding `harness.go`) with no changed `.feature` in
  `pre-commit`/`pre-push`/`ci` (`HARNESS_ALLOW_NO_FEATURE=1` overrides after
  review); it only warns in `check`/`stop-hook`, and skips silently when the
  repo has no `.feature` files anywhere. `crap` is
  advisory (warns by default, `--enforce` to hard-fail). Suppressions are
  ratcheted by `.harness-baseline`; `coverage.min` in the same file is the
  default coverage floor. Requires `uvx` on
  PATH for `complexity`/`crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8,
  length≤100 — replaces the old gocyclo gate).
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

When adapting an existing repo with `make`/`just`, rewrite the prefix but
keep the command names and semantics.

## Bootstrap commands (greenfield)

```bash
# Install golangci-lint v2+ first
brew install golangci-lint  # or: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

cp -r ~/Code/harness-templates/go/ my-project && cd my-project
go mod edit -module my-project
go run harness.go setup-hooks
```

Requires Go 1.24+. This brings `AGENTS.md`/`CLAUDE.md` (Layer 2),
`.claude/settings.json`, `.codex/hooks.json`, and
`.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook; `.codex/hooks.json` wires
the Codex Stop hook. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && go run harness.go stop-hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh go run harness.go stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/go/harness.go`
- Lint config: `~/Code/harness-templates/go/.golangci.yaml` (gosec)
- Tooling: Go compiler typecheck, gofmt, golangci-lint v2, `go test -race`,
  govulncheck, lizard (complexity, via `uvx`), godog (acceptance),
  gremlins (mutation), rapid (property-based tests, see
  `crap/properties_test.go`), go-arch-lint (arch)
- Protected arch config: `.go-arch-lint.yml` (`go run harness.go arch-config-guard`)
