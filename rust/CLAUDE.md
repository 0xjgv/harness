# CLAUDE

- After edits: `cargo harness check` — fix, format, lint, test
- Pre-commit: `cargo harness pre-commit` — staged files only (auto via git hook)
- CI: `cargo harness ci` — read-only lint, format check, tests
- Setup: `cargo harness setup-hooks` to install git hook
