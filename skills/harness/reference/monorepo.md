# monorepo

Source: `~/Code/harness-templates/monorepo/`

Use when the repo holds two or more single-language subprojects, each with
its own harness. The Makefile dispatches to subproject runners — it never
reimplements lint, format, or test logic.

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template (root + every subproject)
hold the same content byte-for-byte (enforced by the harness
`agents-md-drift` check). Both files carry the full contract — Claude
Code reads `CLAUDE.md`; Codex (and other AGENTS.md-consuming tools) read
`AGENTS.md` literally, not as a link. Copy
`~/Code/harness-templates/monorepo/CLAUDE.md` verbatim; do not paraphrase
(it drifts). Two sections:

- `## Commands` — `make check` / `pre-commit` / `pre-push` / `ci` dispatch
  to every subproject; each subproject's own `check` already runs every gate
  that is offline, fast, and takes no build lock (fix, format, typecheck,
  test, complexity, acceptance, its dead-code gate where the language ships
  one — python vulture, bun knip; go and rust cover dead code via their
  linters — plus a lockfile check in python/bun, `go mod tidy -diff` in go,
  and — python/bun only — the architecture boundary check `arch` itself,
  since import-linter/dependency-cruiser are offline and take no build lock),
  and warns via its own `arch-config-guard` and `gherkin-guard`; each
  subproject `ci` runs its read-only gates in parallel (adding `arch` for
  go/rust, which keep it `ci`/`pre-push`-only — go's needs to fetch a module,
  rust's takes cargo's build lock) and its advisory CRAP gate; `pre-push` is the offline
  push gate (lint, format check, acceptance, arch, and strict
  `arch-config-guard` + `gherkin-guard` over the whole pushed tree). `make
  arch-config-guard` protects all known arch config filenames across the
  repo — this is a root-level implementation, not a fan-out. There is no
  equivalent root `make gherkin-guard`: Gherkin-first is enforced per
  subproject, inside each subproject's own `check`/`stop-hook` (warn) and
  `pre-commit`/`pre-push`/`ci` (block via `HARNESS_ALLOW_NO_FEATURE=1`),
  which `make check`/`ci`/etc. already dispatch into. `make crap`
  fans out the advisory CRAP gate directly
  (per-subproject `harness crap`, pass `--enforce` for hard-fail);
  `make agents-md-drift` / `make sync-agents-md` fan out the root +
  per-subproject AGENTS.md ↔ CLAUDE.md drift pair; `make check-<subproject>`
  / `ci-<subproject>` / `pre-push-<subproject>` / `crap-<subproject>` /
  `agents-md-drift-<subproject>` / `sync-agents-md-<subproject>` scope to
  one; `make check-dirty` scopes to changed ones; `PARALLEL=1` opts into
  buffered fan-out; `make list` lists subprojects; `make bootstrap`
  installs everything.
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

Each subproject keeps its own zero-dep harness (`harness.ts` /
`harness.py` / `harness.go` / `cargo harness`). Running one directly from
its own directory still works:

```bash
cd api && uv run harness check
```

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/monorepo/ my-project && cd my-project
git init

# Drop in one or more single-language templates as subprojects:
cp -r ~/Code/harness-templates/python/ api
cp -r ~/Code/harness-templates/bun/    web

make bootstrap   # per-language install + root git hook
make check       # dispatches to every subproject
make check-api   # scope to one subproject
```

The monorepo root `AGENTS.md`/`CLAUDE.md` (Layer 2),
`.claude/settings.json`, `.codex/hooks.json`, and
`.codex/hooks/codex-stop-hook.sh` come in with the copy — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook; `.codex/hooks.json` wires
the Codex Stop hook. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && make stop-hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook`.

The monorepo's `arch-config-guard` protects **all four** arch configs by
basename (`.importlinter`, `.dependency-cruiser.json`, `.go-arch-lint.yml`,
`arch.toml`), suffix-matched so a config nested in any subproject is covered.

## Canonical anchors

- Dispatcher: `~/Code/harness-templates/monorepo/Makefile`
- README: `~/Code/harness-templates/monorepo/README.md`
