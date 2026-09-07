# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The 5-Script Contract

Every template implements the 5 required scripts plus a `stop-hook` target
used by the Stop hook:

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test, suppression ratchet | Yes |
| `pre-commit` | Git pre-commit hook | Staged files only — fix, format, typecheck, test if source changed | Yes |
| `pre-push` | Git pre-push hook | Read-only push gate: lint, format check, acceptance, arch over the whole tree, in parallel | No |
| `ci` | CI pipeline | Read-only gates (lint, typecheck, dep audit, complexity, acceptance, arch) run in parallel, then coverage + advisory CRAP | No |
| `audit` | CI pipeline | Audit dependencies for known vulnerabilities | No |
| `post-edit` | Stop hook helper | Format if source files changed | Yes |
| `stop-hook` | Agent Stop hook | Run `post-edit`, then complexity (+ deadcode where shipped) | Yes |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow. It also ratchets suppression comments (`# noqa`, `// @ts-ignore`, `//nolint`, `#[allow]`, etc.) against `.harness-baseline`: new suppressions fail unless a human signs off on `suppressions --update-baseline`.
**`pre-commit`** runs the same checks scoped to staged files, installed as a git hook.
**`pre-push`** is the read-only push gate — lint, format check, acceptance, arch over the whole pushed tree (the offline checks `pre-commit` and `stop-hook` skip), run in parallel. Installed as a git pre-push hook.
For Go and Bun, the lint gate subsumes format checking.
**`ci`** is the read-only gate — no fixes, just verification. Its read-only gates run in parallel (captured, printed in submission order, run to completion), then coverage streams and CRAP runs advisory.
**`audit`** audits dependencies for known vulnerabilities.
**`post-edit`** formats source files if changed by an agent.
**`stop-hook`** is the Stop hook entrypoint: it runs `post-edit`, then complexity and deadcode where the language ships a separate deadcode gate.

## Autonomous workspace

`make workspace` is the one command that turns a clean clone on a supported VM
into an agent-ready workspace. It supports macOS and glibc Linux on `x86_64`
and `arm64`.

The VM supplies only the bootstrap layer: Make, Bash, Git, curl, tar, Info-ZIP
unzip, a SHA-256 utility, and a writable `HOME`. Rust workspaces also require
`cc`. Workspace setup never uses `sudo` or Homebrew and never edits shell
profiles.

The command has a deterministic contract:

- It requires a Git worktree with a clean tracked/index state. Untracked files
  are preserved.
- It preflights every prerequisite and Git/Stop-hook collision before
  downloading. Unknown Git hooks are refused without modification. Only exact
  legacy harness shims are migrated.
- It ignores ambient tool versions. Exact managed tools live under
  `~/.local/share/harness/tools/<tool>/<version>` and enter `PATH` only while a
  harness command runs.
- It downloads checksum-locked tools, restores dependencies from committed
  locks, deploys the embedded harness skill, and installs the root Git and
  agent Stop hooks.
- It verifies tools, frozen dependencies, skill bytes, and hook wiring; runs
  the normal auto-fixing `make check`; then confirms the tracked/index state is
  still clean. If `make check` changes tracked files, workspace fails and lists
  the changed paths without reverting them.

With a warm tool and dependency cache populated by an earlier online run, the
same convergence can run without network access:

```bash
make workspace OFFLINE=1
```

Offline mode makes no network requests. A cold or incomplete cache fails before
skills or hooks are modified. `make bootstrap` remains a compatibility alias
for `make workspace`.

`make deps` is lock-preserving: it restores only the dependency graph recorded
by the committed native lock files. Dependency upgrades remain explicit native
package-manager operations followed by review of the resulting lock changes.

At the meta-repo and monorepo roots, workspace discovers subprojects once,
provisions the union of their required profiles, and invokes each subproject's
`make deps` exactly once in lexical order. The root owns skill deployment and
root Git/Stop hooks; it never invokes a subproject bootstrap target. CI uses the
same contract: `make workspace` followed by `make ci`.

### Managed tool pins

Artifact URLs and platform SHA-256 digests are committed in
`.harness/workspace.lock`.

| Profile | Exact managed tools |
|---|---|
| Common | uv 0.12.5, CPython 3.13.15, lizard 1.22.2 |
| Python | vulture 2.16, pip-audit 2.10.1 |
| Bun | Bun 1.3.14, knip 5.88.1 |
| Go | Go 1.27.0, golangci-lint 2.12.2, govulncheck 1.1.4, go-arch-lint 1.15.0, gremlins 0.5.0 |
| Rust | rustup 1.28.2, Rust 1.97.1, cargo-audit 0.22.2, cargo-llvm-cov 0.8.7, cargo-modules 0.26.0 |

## Available Templates

| Template | Stack | What workspace manages |
|---|---|---|
| [Python](python/) | Python, ruff, basedpyright, unittest | Exact Python/uv tools and locked dependencies |
| [Bun](bun/) | Bun, Biome, TypeScript | Exact Bun tools and locked dependencies |
| [Go](go/) | Go, golangci-lint | Exact Go tools, analyzers, and locked modules |
| [Rust](rust/) | Rust, clippy, rustfmt | Exact Rust tools, components, and locked crates |
| [Monorepo](monorepo/) | Make dispatcher over any mix of the above | Union of every detected language profile |

## Getting Started

### Python

```bash
cp -r python/ my-project && cd my-project
git init
git add . && git commit -m "Initial template"
make workspace
# Start coding in src/
```

### Bun

```bash
cp -r bun/ my-project && cd my-project
git init
git add . && git commit -m "Initial template"
make workspace
# Start coding in src/
```

### Go

```bash
cp -r go/ my-project && cd my-project
git init
git add . && git commit -m "Initial template"
make workspace
# Start coding
```

### Rust

```bash
cp -r rust/ my-project && cd my-project
git init
git add . && git commit -m "Initial template"
make workspace
# Start coding in src/
```

### Monorepo

```bash
cp -r monorepo/ my-project
cp -r python/ my-project/api
cp -r bun/ my-project/web
cd my-project
git init
git add . && git commit -m "Initial monorepo"
make workspace
make check      # dispatch to every subproject
make check-api  # scope to one subproject
```

## What Each Template Includes

- **Single zero-dep task runner** (`harness.py` / `harness.ts` / `harness.go` / `cargo harness`) — the source of truth, with a thin optional `Makefile` that just forwards to it (`make ci` == `harness ci`)
- **Linter + formatter** — ruff (Python) / Biome (Bun) / golangci-lint (Go) / clippy + rustfmt (Rust)
- **Type checker** — basedpyright (Python) / tsc (Bun) / Go compiler (Go) / Rust compiler (Rust)
- **Test runner** — unittest (Python) / bun test (Bun) / go test (Go) / cargo test (Rust)
- **Security scanning** — bandit rules via ruff (Python) / gosec via golangci-lint (Go) / clippy pedantic + `unsafe_code = "forbid"` (Rust)
- **Dependency auditing** — pip-audit (Python) / bun audit (Bun) / govulncheck (Go) / cargo-audit (Rust) — runs in `ci`
- **Cyclomatic complexity gate** (CCN 15, args 8) — the exact managed lizard pin (Python/Bun/Go/Rust) / gocyclo via the managed golangci-lint pin (Go) — runs in `ci`
- **Dead-code detection** — exact managed vulture (Python) / knip (Bun) pins; Go & Rust use their managed linters (golangci-lint `unused` / clippy `dead_code`) — runs in `ci` + `stop-hook`
- **CRAP advisory** — complexity × coverage signal, advisory by default and still run in `ci`
- **Suppression baseline ratchet** — `.harness-baseline` tracks allowed suppression counts and the coverage floor (`coverage.min`)
- **Arch config guard** — protected architecture config changes warn in `check` / `stop-hook` and fail `pre-commit` / `pre-push` / `ci` unless reviewed with `HARNESS_ALLOW_ARCH_CONFIG=1`
- **Agent Stop hooks** — `.claude/settings.json` and `.codex/hooks.json` enter the Git root and call `make stop-hook`, which supplies the managed environment
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
diverged.

This repo root dogfoods the same Stop-hook shape: `.claude/settings.json` runs
`make stop-hook`, and `.codex/hooks.json` runs the Codex JSON wrapper around
`make stop-hook`. Root `make check` verifies skill drift, root
`AGENTS.md`/`CLAUDE.md` drift, and protected arch-config changes.

## License

[MIT](LICENSE)
