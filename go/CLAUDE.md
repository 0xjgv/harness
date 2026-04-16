# CLAUDE

- After edits: `go run harness.go check` — fix, format, lint, test, suppression report
- Pre-commit: `go run harness.go pre-commit` — staged files only (auto via git hook)
- CI: `go run harness.go ci` — read-only lint, dep audit, tests with race detector and coverage
- Audit: `go run harness.go audit` — audit dependencies for known vulnerabilities (via govulncheck)
- Auto-format: runs automatically after Claude edits via Stop hook (post-edit)
- Setup: `go run harness.go setup-hooks` to install git hook
