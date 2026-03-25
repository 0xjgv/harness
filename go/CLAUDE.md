# CLAUDE

- After edits: `go run harness.go check` — fix, format, lint, test
- Pre-commit: `go run harness.go pre-commit` — staged files only (auto via git hook)
- CI: `go run harness.go ci` — read-only lint, tests with race detector and coverage
- Setup: `go run harness.go hooks` to install git hook
