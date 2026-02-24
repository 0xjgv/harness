# harness

Standalone code complexity metrics engine. Zero required dependencies (Tier 0 uses stdlib only). Computes an Entropy Index (0-100, higher = more complex) per file.

## Stack

- Python 3.10+, Hatchling build
- SQLite3 (WAL mode, 0600 perms)
- radon (Tier 1 metrics)

## Structure

- `harness/cli/main.py` — Top-level CLI router (`harness entropy ...`)
- `harness/cli/measure.py` — `harness entropy measure` CLI entry point
- `harness/cli/report.py` — `harness entropy report` CLI entry point
- `harness/cli/install.py` — `harness entropy install`/`uninstall` CLI entry points
- `harness/cli/seed.py` — `harness entropy seed` CLI entry point
- `harness/cli/hook.py` — Claude Code hook runner (stdin JSON dispatch)
- `harness/config.py` — Constants, defaults, weight vectors, DB path resolution
- `harness/core/db.py` — SQLite storage (schema v1, migrations)
- `harness/core/metrics.py` — Tier 0/1 metric computation
- `harness/core/composite.py` — Entropy Index aggregation (0-100)
- `harness/git.py` — Git helpers (changed files, before/after content)
- `tests/` — pytest suite

## Commands

- `harness entropy measure` — measure entropy index for files
- `harness entropy report` — show trends, hotspots, history
- `harness entropy install` — install Claude Code hooks
- `harness entropy seed` — establish baseline measurements for the project
- `harness entropy uninstall` — remove Claude Code hooks
- `make install` — install with uv (development)
- `make install-global` — install as global CLI tool via `uv tool install`
- `make test` — run pytest
- `make check` — ruff + basedpyright
- `make test-cov` — tests with 80% coverage minimum

## Patterns

- Tiered metrics: Tier 0 (stdlib), Tier 1 (radon), Tier 2 (tree-sitter, future)
- Tier 0+1 always available; Tier 2 redistributes weights when absent
- DB location: `.claude/entropy.db` (project-local, gitignored)
- All DB functions are sync
