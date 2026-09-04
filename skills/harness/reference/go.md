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
  fail on drift). `check` runs fix + format + lint (`--fix`), test, then — as
  a read-only parallel batch — complexity, `go mod tidy -diff` (fails if
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
  advisory (warns by default, `--enforce` to hard-fail). Suppressions,
  complexity, duplication, and CRAP are ratcheted by `.harness-baseline` (see
  below);
  `coverage.min` in the same file is the default coverage floor. Requires `uvx` on
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

## `.harness-baseline` — ratcheted floors

```
complexity.max_violations 0
coverage.min 59
crap.max_violations 0
duplication.max_blocks 0
suppressions.lint_ignore 3
suppressions.nolint 11
```

- **The only writer is `go run harness.go suppressions --update-baseline`.**
  Nobody guesses that from the name — say it out loud when handing the repo
  over. `suppressions` sits in the parity gate's non-allowlistable
  `CORE_COMMANDS`, so a `baseline` command would have to land in all four
  templates; the wart is deliberate.
- **A missing file, or a missing key, is report-only and passes.** The
  complexity gate prints
  `✓ Complexity (lizard, report-only: no .harness-baseline floor)` and exits 0,
  so a repo adopting the harness is green on day one. A floor of 0 inferred
  from an absent number is not a floor, it is a demand that the repo already
  be perfect.
- **`complexity.max_violations` is handed to lizard as `-i N`** — lizard does
  the counting, and exits 0 while the number of flagged functions stays at or
  below the floor. The writer reads the count back out of the `Warning cnt`
  column of lizard's summary row.
- **`duplication.max_blocks`** counts the `Duplicate block:` headers of a
  second lizard run, `-Eduplicate` over the same target set and exclusions as
  the complexity gate (the floor only reproduces against an identical target
  set). It has to be a separate invocation: `-Eduplicate` composes with the
  complexity thresholds but never reaches lizard's exit code, which stays
  driven by CCN warnings alone — so the run passes `-i 1000000` and the runner
  does the comparing. Lizard only reports a block once it spans 70+ unified
  tokens, and overlapping near-duplicates are reported separately, so the count
  jitters by one on a trivial edit; fine for a ratchet, not for an absolute
  number. Report-only under the same rule as complexity.
- **`crap.max_violations`** is compared in the runner, and is report-only under
  the same rule — including under `--enforce`, because an absent key means the
  repo has never been measured, not that it must be perfect. (Python's
  `cmd_crap` uses `_baseline_floor(...) or 0` here, which collapses those two
  cases; go does not, and bun and rust should follow go.)
- **`coverage.min`** is total coverage truncated to an integer, never rounded
  up — a floor above the measured number fails the very next run. It is
  measured first, so the profile it writes is still fresh when the CRAP
  measurement joins against it.
- **The write merges.** Every measured key is overwritten (a suppression kind
  that vanished is recorded as `0`, so the floor ratchets down); every
  unrecognised key — `coverage.min`, `mutation.min`, anything hand-written — is
  carried through untouched.
- **All-or-nothing.** A metric that does not apply here (no `*_test.go`, so no
  CRAP) has its key *dropped* with a `⚠`, never carried forward from the
  shipped template's own numbers. A metric whose tool ran and failed aborts the
  write entirely and exits 1:

```
✗ .harness-baseline not written — could not measure:
    complexity.max_violations: lizard failed to run (exit 2)
  ↳ fix: make the measurement pass, then rerun `suppressions --update-baseline`
```

- Go's table has **4** entries where python's `RATCHETED_KEYS` has 4, but not
  the same four:
  `deadcode.max_findings` has no go equivalent (golangci-lint's `unused` linter
  covers it, and it has no count to floor), and `duplication.max_blocks` is
  go's own.
- Extending it: `harness.go` keeps a `var ratcheted = []suppressions.Measurer`
  table of `{Key, Measure}` pairs. A new floor is one entry plus a function
  returning `suppressions.Measured` / `Unavailable` / `Failed`.

## Canonical anchors

- Runner: `~/Code/harness-templates/go/harness.go`
- Lint config: `~/Code/harness-templates/go/.golangci.yaml` (gosec)
- Tooling: Go compiler typecheck, gofmt, golangci-lint v2, `go test -race`,
  govulncheck, lizard (complexity, via `uvx`), godog (acceptance),
  gremlins (mutation), rapid (property-based tests, see
  `crap/properties_test.go`), go-arch-lint (arch)
- Protected arch config: `.go-arch-lint.yml` (`go run harness.go arch-config-guard`)
