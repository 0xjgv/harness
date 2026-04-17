# CLAUDE

- After edits: `cargo harness check` — fix, format, lint, test, suppression report
- Pre-commit: `cargo harness pre-commit` — staged files only (auto via git hook)
- CI: `cargo harness ci` — read-only lint, format check, dep audit, tests
- Audit: `cargo harness audit` — audit dependencies for known vulnerabilities (via cargo-audit)
- Setup: `cargo harness setup-hooks` to install git hook
- Auto-format: runs automatically after Claude edits via Stop hook (post-edit)
