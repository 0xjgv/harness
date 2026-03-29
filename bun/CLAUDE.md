# CLAUDE

- After edits: `bun run check` — fix, format, typecheck, test
- Pre-commit: `bun run pre-commit` — staged files only (auto via git hook)
- CI: `bun run ci` — read-only lint, typecheck, dep audit, tests with coverage
- Audit: `bun run audit` — audit dependencies for known vulnerabilities (via bun audit)
- Setup: `bun run setup-hooks` to install git pre-commit hook
- Help: `bun harness.ts help` to see all commands
