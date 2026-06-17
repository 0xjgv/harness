# reference-python

Source: `~/Code/harness-templates/python/`

## CLAUDE.md

`AGENTS.md` and `CLAUDE.md` in the template hold the same content
byte-for-byte (enforced by the harness `agents-md-drift` check). Both
files carry the full contract — Claude Code reads `CLAUDE.md`; Codex
(and other AGENTS.md-consuming tools) read `AGENTS.md` literally, not as
a link. Copy `~/Code/harness-templates/python/CLAUDE.md` verbatim; do
not paraphrase (it drifts). Two sections:

- `## Commands` — `check`, `pre-commit`, `ci`, `audit`, plus quality
  subcommands `complexity`, `acceptance`, `coverage`, `mutation`, `crap`,
  `arch`, and the drift pair `agents-md-drift` / `sync-agents-md` (keeps
  `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit` fail
  on drift). `test` runs `unittest`, or `py_compile` over `src/` and
  `harness.py` when no `tests/test*.py` files exist. `ci` runs the
  pipeline `lint → format check → typecheck → audit → complexity →
  acceptance → coverage → crap → arch`; `crap` is advisory (warns by
  default, `--enforce` to hard-fail) but still runs in `ci`. Requires
  `uvx` on PATH for `complexity`/`crap` (lizard pinned to `1.22.2`,
  CCN≤15, args≤8, length≤100).
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

This brings `.claude/` (Layer 2) intact — keep it.

## Hooks

`.claude/settings.json` wires all 5 hooks. Full shape:
[reference-settings-json.md](reference-settings-json.md).
Stop commands:
`cd $CLAUDE_PROJECT_DIR && uv run harness post-edit`;
`cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/python/harness.py`
- Quiet-output `run()` pattern: `~/Code/harness-templates/python/harness.py`
- Tooling: uv, ruff (lint + format + bandit-style security), basedpyright,
  unittest, coverage, pip-audit, lizard (complexity, via `uvx`), behave
  (acceptance), mutmut (mutation), hypothesis (property-based tests, see
  `tests/test_properties.py`), import-linter (arch)
- Protected arch config: `.importlinter`
