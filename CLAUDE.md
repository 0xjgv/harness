# entropy-meter

Standalone code complexity metrics engine. Zero required dependencies (Tier 0 uses stdlib only). Computes an Entropy Index (0-100, higher = more complex) per file.

## Stack

- Python 3.10+, Hatchling build
- SQLite3 (WAL mode, 0600 perms)
- Optional: radon (Tier 1 metrics)

## Structure

- `entropy_meter/config.py` — Constants, defaults, weight vectors, DB path resolution
- `entropy_meter/core/db.py` — SQLite storage (schema v1, migrations)
- `entropy_meter/core/metrics.py` — Tier 0/1 metric computation
- `entropy_meter/core/composite.py` — Entropy Index aggregation (0-100)
- `entropy_meter/cli/measure.py` — `entropy-measure` CLI entry point
- `entropy_meter/cli/report.py` — `entropy-report` CLI entry point
- `entropy_meter/git.py` — Git helpers (changed files, before/after content)
- `tests/` — pytest suite

## Commands

- `make install` — install with uv (development)
- `make install-global` — install as global CLI tool via `uv tool install`
- `make test` — run pytest
- `make check` — ruff + mypy
- `make test-cov` — tests with 80% coverage minimum

## Patterns

- Tiered metrics: Tier 0 (stdlib), Tier 1 (radon), Tier 2 (tree-sitter, future)
- Unavailable tiers redistribute weights proportionally
- DB location: `.claude/entropy.db` (project-local, gitignored)
- All DB functions are sync
