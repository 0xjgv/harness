# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The Seven-Stage Contract

Every template implements the same seven stages, via its own zero-dependency
task runner:

| Stage | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test, plus every other gate that is offline, fast, and takes no build lock (complexity, acceptance, deadcode where shipped, a lockfile check in Python/Bun, `go mod tidy -diff` in Go, and — Python/Bun only — the architecture boundary check `arch` itself); warns on `arch-config-guard` and `gherkin-guard`; checks AGENTS.md/CLAUDE.md drift; ratchets suppressions | Yes |
| `pre-commit` | Git pre-commit hook | Staged files only — fix, format, typecheck, test if source changed, then re-stages (`git add`) the files it fixed | Yes |
| `pre-push` | Git pre-push hook | Read-only push gate: lint, format check, acceptance, arch over the whole tree, in parallel; strict `arch-config-guard` + `gherkin-guard` | No |
| `ci` | CI pipeline | Read-only gates (lint, typecheck, dep audit, complexity, deadcode where shipped, acceptance, arch) run in parallel, then coverage + advisory CRAP; strict `arch-config-guard` + `gherkin-guard` | No |
| `audit` | CI pipeline | Audit dependencies for known vulnerabilities | No |
| `post-edit` | Stop hook helper | Format if source files changed | Yes |
| `stop-hook` | Agent Stop hook | Run `post-edit`, then complexity (+ deadcode where shipped); **exits 2 with a failure summary on stderr** | Yes |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow, then runs every other gate that doesn't need the network or a build lock. It also ratchets suppression comments (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`, etc.) against `.harness-baseline`: new suppressions fail unless a human signs off on `suppressions --update-baseline`. Invariant: `ci` minus `check` is only the network dependency audit, coverage over the whole test suite (where `check` ran only the tests mapped to the change set), advisory CRAP, and advisory mutation — plus, in Go and Rust only, the architecture boundary check itself (`arch`), which stays `ci`/`pre-push`-only there (Go's needs to fetch a module, Rust's takes cargo's build lock); Python and Bun's `arch` has neither constraint, so it runs inside `check` too — so a green `check` predicts a green `ci` for the gates it ran. `check` runs only the tests that map to the change set, so `check --all` (or `ci`) is the whole-suite run; `pre-push` has no test gate in any template.
**`pre-commit`** runs the same checks scoped to staged files, installed as a git hook. It re-stages whatever it fixes, so the commit records the fixed content — the same trade-off `lint-staged` makes: a partially staged file gets its unstaged hunks staged too.
**`pre-push`** is the read-only push gate — lint, format check, acceptance, arch over the whole pushed tree (the offline checks `pre-commit` and `stop-hook` skip), run in parallel. Installed as a git pre-push hook.
For Go and Bun, the lint gate subsumes format checking.
**`ci`** is the read-only gate — no fixes, just verification. Its read-only gates run in parallel (captured, printed in submission order, run to completion), then coverage streams and CRAP runs advisory.
**`audit`** audits dependencies for known vulnerabilities.
**`post-edit`** formats source files if changed by an agent, using repo-root-relative paths so it also works when the harness lives in a subdirectory (e.g. a `monorepo/` subproject).
**`stop-hook`** is the Stop hook entrypoint: it runs `post-edit`, then complexity and deadcode where the language ships a separate deadcode gate.

### Stop hook failures now reach the agent

Every runner's `stop-hook` subcommand exits **2** on failure and writes a short
failure summary to stderr, and every Claude Stop-hook command ends in
`|| exit 2`. This matters because Claude Code only treats a Stop hook's exit
code **2** as blocking — it feeds the hook's stderr back to the model so the
agent has to address the failure before stopping. Any other non-zero exit is
a non-blocking error the model never sees, so a runner that failed with exit 1
used to fail silently. The `|| exit 2` suffix is required because `go run`
collapses its child process's exit code (it prints `exit status N` to stderr,
then exits 1 itself, no matter what the compiled program returned) — measured,
not theoretical. For runners where propagation already worked (Python, Bun,
Rust, `make`) the suffix is a no-op, so all five templates carry it for
consistency. Full shape: [settings-json.md](skills/harness/reference/settings-json.md).

## Available Templates

| Template | Stack | Prerequisites |
|---|---|---|
| [Python](python/) | uv, ruff, basedpyright, unittest | [uv](https://docs.astral.sh/uv/) |
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

- **Single zero-dep task runner** (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`) — the source of truth, with a thin optional `Makefile` that just forwards to it (`make ci` == `harness ci`)
- **Linter + formatter** — ruff (Python) / Biome (Bun) / golangci-lint (Go) / clippy + rustfmt (Rust)
- **Type checker** — basedpyright (Python) / tsc (Bun) / Go compiler (Go) / Rust compiler (Rust)
- **Test runner** — unittest (Python) / bun test (Bun) / go test (Go) / cargo test (Rust)
- **Security scanning** — bandit rules via ruff (Python) / gosec via golangci-lint (Go) / clippy pedantic + `unsafe_code = "forbid"` (Rust)
- **Dependency auditing** — pip-audit (Python) / bun audit (Bun) / govulncheck (Go) / cargo-audit (Rust) — runs in `ci`
- **Lockfile / module-tidy drift check** — `uv lock --check` (Python) / `bun install --frozen-lockfile` (Bun) / `go mod tidy -diff` (Go) — runs in `check`
- **Cyclomatic complexity gate** (CCN 15, args 8) — lizard via `uvx`, all four languages — runs in `check` + `ci`
- **Dead-code detection** — vulture (Python, via `uvx`) / knip (Bun, via `bunx`); Go & Rust use their linters (golangci-lint `unused` / clippy `dead_code`) — runs in `check` + `ci` + `stop-hook`
- **Acceptance tests** — behave (Python) / cucumber (Bun) / godog (Go) / cucumber (Rust); Rust's runs under plain `cargo test` as a `[[test]]` target, so Rust has no separate `acceptance` gate in `check` — runs in `check` + `ci` + `pre-push`
- **CRAP advisory** — complexity × coverage signal, advisory by default (prints a green `⚠`, not a red `✗`, unless `--enforce`) and still run in `ci`
- **Suppression baseline ratchet** — `.harness-baseline` tracks allowed suppression counts and the coverage floor (`coverage.min`)
- **Arch config guard** — protected architecture config changes warn in `check` / `stop-hook` and fail `pre-commit` / `pre-push` / `ci` unless reviewed with `HARNESS_ALLOW_ARCH_CONFIG=1`; the diff base falls back through `origin/HEAD` → `origin/main` → `main` when no PR base ref is set (e.g. a direct push to `main`)
- **Gherkin-first guard** — a changed production-source file with no accompanying `.feature` change warns in `check` / `stop-hook` and fails `pre-commit` / `pre-push` / `ci` unless reviewed with `HARNESS_ALLOW_NO_FEATURE=1`; skips silently when the repo has no `.feature` files yet
- **Agent Stop hooks** — `.claude/settings.json` runs `stop-hook`; `.codex/hooks.json` runs the Codex JSON wrapper around `stop-hook` (see [above](#stop-hook-failures-now-reach-the-agent))
- **Property-based testing** — hypothesis (Python) / fast-check (Bun) / rapid (Go) / proptest (Rust), seeded with a property suite over each template's own CRAP and parser helpers as the worked example; runs under the normal `test` step
- **AGENTS.md + CLAUDE.md** — tell AI agents which commands to run and when

## Design Principles

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only

## Harness skill

The skill that bootstraps repos to match these templates lives in
`skills/harness/`. Edit there; run `make sync-skills` to deploy to
`~/.claude/skills/harness/` and `~/.agents/skills/harness/`. `make
skills-drift` (run by `make check`) fails if the deployed copies have
diverged. `HARNESS_SKIP_SKILLS_DRIFT=1` skips that check on hosts with no
deployed skill copies under `$HOME` (CI runners, for example).

`make parity` (`scripts/parity-gate.sh`, a prerequisite of `make check`)
statically checks that the four templates' command surfaces stay in sync: every
runner-dispatched command appears in that template's Makefile
`HARNESS_TARGETS`, every command a template's `CLAUDE.md` documents exists in
its runner, every `bun run <x>` in `bun/CLAUDE.md` has a matching
`bun/package.json` script, all four templates expose the same core 19
commands, and any other cross-template command divergence is either present
in all four or explicitly allowlisted with a reason in the script.

This repo root dogfoods the same Stop-hook shape: `.claude/settings.json` runs
`make stop-hook`, and `.codex/hooks.json` runs the Codex JSON wrapper around
`make stop-hook`. Root `make check` verifies skill drift, root
`AGENTS.md`/`CLAUDE.md` drift, command-surface parity, and protected
arch-config changes. This root also has its own CI
(`.github/workflows/ci.yml`), which installs every template's toolchain in one
job and runs `make check` followed by `make ci` — root `make ci` runs the
same repo-level gates plus every template's own `ci`.

## License

[MIT](LICENSE)
