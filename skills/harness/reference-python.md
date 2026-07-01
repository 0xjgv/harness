# reference-python

Source: `~/Code/harness-templates/python/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/python/CLAUDE.md` verbatim; do
not paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `pre-push`, `ci`, `audit`, plus
  quality subcommands `complexity`, `deadcode`, `acceptance`, `coverage`,
  `mutation`, `crap`, `arch`, `suppressions`, and the drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift). `test` runs `unittest`, or `py_compile` over `src/` and
  `harness.py` when no `tests/test*.py` files exist. `ci` runs the
  read-only gates (`lint`, `format check`, `typecheck`, `audit`,
  `complexity`, `deadcode`, `acceptance`, `arch`) **in parallel** — captured
  and printed in submission order, run to completion so one pass surfaces
  every failure — then streams `coverage` and the advisory `crap`.
  `pre-push` is the offline push gate: `lint`, `format check`, `acceptance`,
  `arch` over the whole pushed tree (the deterministic checks pre-commit and
  stop-hook skip). `deadcode` runs vulture (pinned `2.16`) over `src/` only —
  never `tests/`, so a dead helper that still has a test is reported, not
  masked — at `--min-confidence 60`; allowlist dynamic references
  (decorator-registered handlers, getattr dispatch) in `vulture_allowlist.py`.
  It runs in `ci` and `stop-hook`. `crap` is advisory (warns by default,
  `--enforce` to hard-fail) but runs in `ci`, not `stop-hook`. Suppressions
  are ratcheted by `.harness-baseline`; `coverage.min` in the same file is
  the default coverage floor. Requires `uvx` on PATH
  for `complexity`/`crap`/`deadcode` (lizard pinned to `1.22.2`, CCN≤15,
  args≤8, length≤100).
- `## Behavior contract` — Layer 2; see
  [reference-behavior-contract.md](reference-behavior-contract.md).

When adapting an existing repo, rewrite the `uv run harness …` prefix to
match the repo's runner (e.g. `just check`, `make check`) but keep the
command names and their semantics.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/python/ my-project && cd my-project
uv sync && uv run harness setup-hooks
# Start coding in src/
```

This brings `.claude/` (Layer 2), `.codex/hooks.json`, and
`.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires Claude hooks; `.codex/hooks.json` wires the
Codex Stop hook. Full shape:
[reference-settings-json.md](reference-settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh uv run harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/python/harness.py`
- Quiet-output `run()` pattern: `~/Code/harness-templates/python/harness.py`
- Tooling: uv, ruff (lint + format + bandit-style security), basedpyright,
  unittest, coverage, pip-audit, lizard (complexity, via `uvx`), vulture
  (dead code, via `uvx`), behave (acceptance), mutmut (mutation), hypothesis
  (property-based tests, see `tests/test_properties.py`), import-linter (arch)
- Protected arch config: `.importlinter`
- Dead-code allowlist: `vulture_allowlist.py`
