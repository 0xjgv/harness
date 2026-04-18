# CLAUDE

- After edits: `make check` — dispatches `check` to every subproject (fix, format, typecheck, test, suppression report)
- Pre-commit: `make pre-commit` — runs only in subprojects with staged files (auto via git hook)
- CI: `make ci` — read-only gate across every subproject (lint, typecheck, dep audit, tests with coverage)
- Scope to one subproject: `make check-<subproject>` (e.g. `make check-api`, `make ci-web`)
- Scope to dirty subprojects: `make check-dirty` (working-tree + untracked changes)
- Parallel fan-out: `PARALLEL=1 make check` — opt-in, buffered per-subproject output. Keep off for CI and agent-visible runs.
- List subprojects: `make list`
- Setup: `make bootstrap` — per-language install + install the root git hook
- Auto-format: runs automatically after Claude edits via Stop hook (`make post-edit`)

Each subproject keeps its own zero-dep harness (`harness.ts` / `harness.py` / `harness.go` / `cargo harness`). The Makefile only dispatches — never reimplements lint, format, or test logic. Running a subproject's harness directly from its own directory still works:

```bash
cd api && uv run harness check
```
