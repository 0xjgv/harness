# CLAUDE

- After edits: `uv run harness check` — fix, format, typecheck, test
- Pre-commit: `uv run harness pre-commit` — staged files only (auto via git hook)
- CI: `uv run harness ci` — read-only lint, format check, typecheck, tests with coverage
- Setup: `uv run harness setup-hooks` to install git pre-commit hook
- Help: `uv run harness help` to see all commands
