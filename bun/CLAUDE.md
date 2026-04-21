# CLAUDE

- After edits: `bun run check` — fix, format, typecheck, test, suppression report
- Pre-commit: `bun run pre-commit` — staged files only (auto via git hook)
- CI: `bun run ci` — read-only lint, typecheck, dep audit, complexity gate (lizard, CCN 15), tests with coverage. Requires `uvx` on PATH.
- Audit: `bun run audit` — audit dependencies for known vulnerabilities (via bun audit)
- Setup: `bun run setup-hooks` to install git pre-commit hook
- Auto-format: runs automatically after Claude edits via Stop hook (post-edit)
