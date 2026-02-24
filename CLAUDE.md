# harness

Standalone code complexity metrics engine. Zero required dependencies (Tier 0 uses stdlib only). Computes an Entropy Index (0-100, higher = more complex) per file.

## Stack

- Python 3.13+, Hatchling build
- SQLite3 (WAL mode, 0600 perms)
- radon (Tier 1 metrics)

## Structure

- `harness/cli/` — One module per subcommand + hook runner & self-healing
- `harness/core/` — Metrics computation, composite scoring, SQLite storage
- `harness/config.py` — Constants, weights, DB path resolution
- `harness/git.py` — Git helpers (changed files, before/after content)
- `tests/` — pytest suite

## Commands

- Build: `make install` (dev) / `make install-global` (global CLI)
- Test: `make test` / `make test-cov` (80% minimum)
- Lint: `make check` (ruff + basedpyright)

## Patterns

- Tiered metrics: Tier 0 (stdlib), Tier 1 (radon), Tier 2 (tree-sitter, future)
- Tier 0+1 always available; Tier 2 redistributes weights when absent
- DB location: `.claude/entropy.db` (project-local, gitignored)
- All DB functions are sync

## Coding Guidelines

- Always write tests that reproduce the issue/bug we are fixing.
