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
  `crap`, `arch`, `arch-config-guard`, `branch-guard`, `suppressions`, and the drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift). `ci` runs the read-only gates (`lint`, `audit`,
  `complexity`, `acceptance`, `arch`) **in parallel** — captured and
  printed in submission order, run to completion so one pass surfaces every
  failure — then streams `coverage` and the advisory `crap`; `ci` also runs
  `arch-config-guard` in strict mode.
  `pre-commit` fixes and formats the staged packages; it no longer runs
  tests. A staged `CLAUDE.md` is copied to `AGENTS.md` and staged with it; a
  hand edit to `AGENTS.md` alone fails the drift check.
  `pre-push` is the offline push gate: after `branch-guard` refuses
  `main`/`master`, it runs the test suite (git's `GIT_*` hook variables
  stripped, since tests run `git init` in temp dirs; a failure stops the
  push), then `lint` (golangci-lint covers format), `acceptance`, `arch`,
  and strict `arch-config-guard` over the whole pushed tree (the
  deterministic checks pre-commit and stop-hook skip).
  `stop-hook` runs post-edit (`golangci-lint fmt` on the changed Go files,
  `golangci-lint run --fix --new-from-rev=HEAD` over their packages only),
  then two read-only gates over the lines changed since the merge-base with
  the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`, then
  `origin/HEAD`, `origin/main`, `origin/master`, `main`, `master`; never
  fetched) plus untracked files. Lint: `golangci-lint run
  --new-from-rev=<merge-base>` (JSON) over the changed packages, kept to
  changed lines; `unused` covers dead code, `gocyclo` is left to the
  complexity gate, and compile errors (`typecheck`) always block.
  Complexity: `lizard --csv` on changed non-test files; any function over a
  limit whose line span overlaps a changed range blocks, so touching an
  over-limit function means leaving it better, and untouched debt never
  blocks. It prints nothing on success and exits 2 with findings on stderr
  (at most 20 lines). Exit 1 means a tool could not run, or the event has
  `stop_hook_active: true` (this stop already blocked once). An uncommitted
  `CLAUDE.md` edit is copied to `AGENTS.md`. No arch-config warning and no
  whole-tree gates at stop.
  `post-edit --hook` (Claude PostToolUse) formats and fixes the one file the
  event names and prints one `additionalContext` line when it changed. It
  never blocks. There is **no** `deadcode` target — golangci-lint's
  `unused` linter (run by the `lint` gate) already flags unreachable
  functions, vars, and types, and `go mod tidy` prunes unused dependencies;
  `x/tools/cmd/deadcode` only works on programs with a `main` package, not
  this library template. `crap` is
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

`.claude/settings.json` wires the Claude Stop hook (`timeout` 300) and the
Claude PostToolUse hook (`matcher` `Edit|Write`, `timeout` 60);
`.codex/hooks.json` wires the Codex Stop hook only. `setup-hooks` installs the
two Stop hooks idempotently; the PostToolUse hook ships in the checked-in
settings file. `check` warns when any of the three is missing. Full shape:
[settings-json.md](settings-json.md).
The Stop commands build the runner and run the binary. `go run` exits 1 for
any non-zero exit of the program (and prints `exit status 2`), so
`go run harness.go stop-hook` can never block. A failed build exits 1, which
does not block either. The `harness` binary is gitignored.
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && go build -o harness harness.go && ./harness stop-hook`.
Claude PostToolUse command (always exits 0, so `go run` is fine):
`cd $CLAUDE_PROJECT_DIR && go run harness.go post-edit --hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && go build -o harness harness.go && .codex/hooks/codex-stop-hook.sh ./harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/go/harness.go`
- Lint config: `~/Code/harness-templates/go/.golangci.yaml` (gosec)
- Tooling: Go compiler typecheck, gofmt, golangci-lint v2, `go test -race`,
  govulncheck, lizard (complexity, via `uvx`), godog (acceptance),
  gremlins (mutation), rapid (property-based tests, see
  `crap/properties_test.go`), go-arch-lint (arch)
- Protected arch config: `.go-arch-lint.yml` (`go run harness.go arch-config-guard`)
- Branch guard: `go run harness.go branch-guard` (warns nothing, fails on `main`/`master`; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides)
