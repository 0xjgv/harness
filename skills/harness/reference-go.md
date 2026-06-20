# reference-go

Source: `~/Code/harness-templates/go/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/go/CLAUDE.md` verbatim; do not
paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `ci`, `audit`, plus quality
  subcommands `complexity`, `acceptance`, `test-cov`, `mutation`, `crap`,
  `arch`, and the drift pair `agents-md-drift` / `sync-agents-md` (keeps
  `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit` fail
  on drift). `ci` pipeline is `lint → audit → complexity → acceptance →
  test-cov → crap → arch`; `crap` is advisory (warns by default,
  `--enforce` to hard-fail). Requires `uvx` on PATH for `complexity`/
  `crap` (lizard pinned to `1.22.2`, CCN≤15, args≤8, length≤100 —
  replaces the old gocyclo gate).
- `## Behavior contract` — Layer 2; see
  [reference-behavior-contract.md](reference-behavior-contract.md).

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

Requires Go 1.24+. This brings `.claude/` (Layer 2), `.codex/hooks.json`,
and `.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires Claude hooks; `.codex/hooks.json` wires the
Codex Stop hook. Full shape:
[reference-settings-json.md](reference-settings-json.md).
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
- Protected arch config: `.go-arch-lint.yml`
