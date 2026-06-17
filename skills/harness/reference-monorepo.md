# reference-monorepo

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

- `## Commands` — `make check` / `pre-commit` / `ci` dispatch to every
  subproject; each subproject `ci` includes its advisory CRAP gate.
  `make crap` fans out the advisory CRAP gate directly (per-subproject
  `harness crap`, pass `--enforce` for hard-fail); `make agents-md-drift`
  / `make sync-agents-md` fan out the root + per-subproject AGENTS.md ↔
  CLAUDE.md drift pair; `make check-<subproject>` / `ci-<subproject>` /
  `crap-<subproject>` / `agents-md-drift-<subproject>` /
  `sync-agents-md-<subproject>` scope to one; `make check-dirty` scopes
  to changed ones; `PARALLEL=1` opts into buffered fan-out; `make list`
  lists subprojects; `make bootstrap` installs everything.
- `## Behavior contract` — Layer 2; see
  [reference-behavior-contract.md](reference-behavior-contract.md).

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

The monorepo root `.claude/` (Layer 2) comes in with the copy — keep it.

## Hooks

`.claude/settings.json` wires all 5 hooks. Full shape:
[reference-settings-json.md](reference-settings-json.md).
Stop commands:
`cd $CLAUDE_PROJECT_DIR && make post-edit`;
`cd $CLAUDE_PROJECT_DIR && make stop-hook`.

The monorepo's `pre-edit-gate.sh` and `ups-classify.sh` protect **all
four** arch configs by basename (`.importlinter`,
`.dependency-cruiser.json`, `.go-arch-lint.yml`, `arch.toml`),
suffix-matched so a config nested in any subproject is covered.

## Canonical anchors

- Dispatcher: `~/Code/harness-templates/monorepo/Makefile`
- README: `~/Code/harness-templates/monorepo/README.md`
