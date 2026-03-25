# harness-templates

Opinionated project templates with built-in quality guardrails for AI coding agents.

## Problem

AI agents write code fast but without feedback loops they drift — formatting breaks, types rot, tests fail silently. These templates give every project a consistent harness that agents (and humans) can run after every edit.

## The 3-Script Contract

Every template implements exactly 3 scripts:

| Script | When | What it does | Fixes code? |
|---|---|---|---|
| `check` | After edits | Fix, format, typecheck, test | Yes |
| `pre-commit` | Git hook | Staged files only — fix, format, typecheck, test if source changed | Yes |
| `ci` | CI pipeline | Read-only lint, typecheck, tests with coverage | No |

**`check`** is the one you run constantly. It auto-fixes what it can so you stay in flow.
**`pre-commit`** runs the same checks scoped to staged files, installed as a git hook.
**`ci`** is the read-only gate — no fixes, just verification.

## Available Templates

| Template | Stack |
|---|---|
| [Python](python/) | uv, ruff, basedpyright, pytest |
| [Bun](bun/) | Bun, Biome, TypeScript |

## Getting Started

### Python

```bash
cp -r python/ my-project && cd my-project
uv sync && uv run hooks
# Start coding in src/
```

### Bun

```bash
cp -r bun/ my-project && cd my-project
bun install && bun run hooks
# Start coding in src/
```

## What Each Template Includes

- **Single zero-dep task runner** (`harness.py` / `harness.ts`) — no Makefile, no task framework
- **Linter + formatter** — ruff (Python) / Biome (Bun)
- **Type checker** — basedpyright (Python) / tsc (Bun)
- **Test runner** — pytest (Python) / bun test (Bun)
- **CLAUDE.md** — tells AI agents which commands to run and when

## Design Principles

- **Zero external dependencies in the runner** — stdlib/runtime APIs only
- **Quiet by default** — only errors shown, `--verbose` for everything
- **Fix what you can** — `check` and `pre-commit` auto-fix; `ci` is read-only
