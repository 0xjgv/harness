# python

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
  `mutation`, `crap`, `arch`, `arch-config-guard`, `branch-guard`, `suppressions`, and the drift pair `agents-md-drift` / `sync-agents-md`
  (keeps `AGENTS.md` byte-identical to `CLAUDE.md`; `check` + `pre-commit`
  fail on drift). `test` runs `unittest`, or `py_compile` over `src/` and
  `harness.py` when no `tests/test*.py` files exist. `ci` runs the
  read-only gates (`lint`, `format check`, `typecheck`, `audit`,
  `complexity`, `deadcode`, `acceptance`, `arch`) **in parallel** — captured
  and printed in submission order, run to completion so one pass surfaces
  every failure — then streams `coverage` and the advisory `crap`; `ci`
  also runs `arch-config-guard` in strict mode.
  `pre-commit` fixes and formats staged files and typechecks; it no longer
  runs tests. A staged `CLAUDE.md` is copied to `AGENTS.md` and staged with
  it; a hand edit to `AGENTS.md` alone fails the drift check.
  `pre-push` is the offline push gate: after `branch-guard` refuses
  `main`/`master`, it runs the test suite (alone, with git's `GIT_*` hook
  variables stripped, since the suite writes caches and runs `git init` in
  temp dirs), then `lint`, `format check`, `acceptance`, `arch`, and strict
  `arch-config-guard` over the whole pushed tree (the deterministic checks
  pre-commit and stop-hook skip).
  `stop-hook` runs post-edit, then changed-lines lint, complexity delta, and
  deadcode delta. It prints nothing on success and exits 2 with findings on
  stderr (at most 20 lines). Changed lines come from `git diff -U0` against
  the merge-base with the base branch (`HARNESS_ARCH_BASE`, `GITHUB_BASE_REF`,
  then `origin/HEAD`, `origin/main`, `origin/master`, `main`, `master`; never
  fetched), plus untracked files. Each delta gate keeps the targets of its
  whole-tree counterpart. Lint residue runs `ruff check` JSON on changed
  project files (`src/`, `harness.py`, `tests/`) and keeps findings on changed
  lines. The complexity delta runs `lizard --csv` on changed `src/` and `tests/`
  files, now and at the base (`git show <base>:./<path>`). It keys functions by
  `long_name`. When a signature is missing at the base, it falls back to the
  function name, but only if that name is unique in both the current and base
  file. A function blocks only when it is over a limit and new or worse than at
  the base. The dead-code delta runs vulture over `src/` as `ci` does, and only
  when a `src/` file changed. It keeps the findings on changed lines. Exit 1 means a tool
  could not run, or the same payload came back with `stop_hook_active: true`
  (loop guard, state under `git rev-parse --git-path harness`). An
  uncommitted `CLAUDE.md` edit is copied to `AGENTS.md`. No arch-config warning
  and no whole-tree gates at stop.
  `post-edit --hook` (Claude PostToolUse) fixes and formats the one file the
  event names and prints one `additionalContext` line when it changed. It
  never blocks. `deadcode` runs vulture (pinned `2.16`) over `src/` only —
  never `tests/`, so a dead helper that still has a test is reported, not
  masked — at `--min-confidence 60`; allowlist dynamic references
  (decorator-registered handlers, getattr dispatch) in `vulture_allowlist.py`.
  It runs in `ci`; `stop-hook` keeps only findings on changed lines. `crap` is advisory (warns by default,
  `--enforce` to hard-fail) but runs in `ci`, not `stop-hook`. Suppressions
  are ratcheted by `.harness-baseline`; `coverage.min` in the same file is
  the default coverage floor. Requires `uvx` on PATH
  for `complexity`/`crap`/`deadcode` (lizard pinned to `1.22.2`, CCN≤15,
  args≤8, length≤100).
- `## Behavior contract` — Layer 2; see
  [behavior-contract.md](behavior-contract.md).

When adapting an existing repo, rewrite the `uv run harness …` prefix to
match the repo's runner (e.g. `just check`, `make check`) but keep the
command names and their semantics.

## Bootstrap commands (greenfield)

```bash
cp -r ~/Code/harness-templates/python/ my-project && cd my-project
uv sync && uv run harness setup-hooks
# Start coding in src/
```

This brings `AGENTS.md`/`CLAUDE.md` (Layer 2), `.claude/settings.json`,
`.codex/hooks.json`, and `.codex/hooks/codex-stop-hook.sh` intact — keep them.

## Hooks

`.claude/settings.json` wires the Claude Stop hook (`timeout` 300) and the
Claude PostToolUse hook (`matcher` `Edit|Write`, `timeout` 60);
`.codex/hooks.json` wires the Codex Stop hook only. `setup-hooks` installs all
three idempotently, and `check` warns when any is missing. Full shape:
[settings-json.md](settings-json.md).
Claude Stop command:
`cd $CLAUDE_PROJECT_DIR && uv run harness stop-hook`.
Claude PostToolUse command:
`cd $CLAUDE_PROJECT_DIR && uv run harness post-edit --hook`.
Codex Stop command:
`cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh uv run harness stop-hook`.

## Canonical anchors

- Runner: `~/Code/harness-templates/python/harness.py`
- Quiet-output `run()` pattern: `~/Code/harness-templates/python/harness.py`
- Tooling: uv, ruff (lint + format + bandit-style security), basedpyright,
  unittest, coverage, pip-audit, lizard (complexity, via `uvx`), vulture
  (dead code, via `uvx`), behave (acceptance), mutmut (mutation), hypothesis
  (property-based tests, see `tests/test_properties.py`), import-linter (arch)
- Protected arch config: `.importlinter` (`uv run harness arch-config-guard`)
- Branch guard: `uv run harness branch-guard` (warns nothing, fails on `main`/`master`; `HARNESS_ALLOW_PROTECTED_PUSH=1` overrides)
- Dead-code allowlist: `vulture_allowlist.py`
