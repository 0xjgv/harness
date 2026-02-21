# entropy-meter

Code complexity metrics engine for Python. Computes an **Entropy Index (0-100)** per file — higher means more complex. Zero required dependencies; optional extras unlock deeper analysis.

Designed to run as a post-commit hook for [Claude Code](https://docs.anthropic.com/en/docs/claude-code), providing real-time feedback on complexity changes. Also works standalone via CLI or Python API.

## Install

```bash
# Minimal (Tier 0 — stdlib only)
pip install -e .

# With structural analysis (Tier 1)
pip install -e ".[radon]"

# Everything
pip install -e ".[all]"
```

## Quick Start

### CLI

```bash
# Measure specific files
entropy-measure src/app.py src/utils.py

# Measure files changed in the last commit
entropy-measure --commit HEAD

# Measure all Python files in a project
entropy-measure --all

# Store results for trend tracking
entropy-measure --commit HEAD --store

# View trends
entropy-report
entropy-report --hotspots
entropy-report --file src/app.py
```

### Python API

```python
from pathlib import Path
from entropy_meter import measure_file, compute_entropy_index

metrics = measure_file(Path("src/app.py"))
ei = compute_entropy_index(metrics)
print(f"Entropy Index: {ei}")  # 0-100
```

Or measure from string content (useful for git blobs):

```python
metrics = measure_file(content="def foo():\n    return 42\n")
ei = compute_entropy_index(metrics)
```

## Tiered Metrics

Metrics degrade gracefully when optional dependencies are unavailable. Weights from missing tiers redistribute proportionally among available tiers.

| Tier | Metrics | Dependency | Cost/file |
|------|---------|------------|-----------|
| **0** | Compression ratio, line count, blank/comment lines, line length stddev, Shannon entropy | stdlib | <0.1ms |
| **1** | Cyclomatic complexity, Maintainability Index, Halstead volume | `radon` | ~2ms |

### Entropy Index

Weighted sum of normalized metrics, scaled to 0-100:

| Metric | Weight | Tier |
|--------|--------|------|
| Compression ratio | 0.25 | 0 |
| Line entropy | 0.15 | 0 |
| Cyclomatic complexity | 0.20 | 1 |
| Maintainability Index | 0.15 | 1 |
| Halstead volume | 0.10 | 1 |

With Tier 0 only, the two Tier 0 metrics share 100% of the weight (0.625 and 0.375 after redistribution).

## Claude Code Integration

entropy-meter is designed to power a post-commit hook in Claude Code projects. The hook:

1. Fires after every `git commit`
2. Measures changed Python files
3. Computes EI deltas against previous measurements
4. Provides feedback using an **asymmetric neutral band**:

| Delta EI | Behavior |
|----------|----------|
| <= -5 | Positive reinforcement |
| -5 to +2 | **Silence** (no output) |
| +2 to +10 | Informational |
| +10 to +25 | Actionable suggestion |
| > +25 | Warning with guidance |

### Setup

No files to copy per project. All projects point at the single canonical script in this repo.

1. Install entropy-meter in the target project's venv:

```bash
uv pip install -e ~/Code/entropy-meter
```

2. Merge the hook config into the project's `.claude/settings.local.json` (snippet in [`examples/hooks_settings.json`](examples/hooks_settings.json)):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{
          "type": "command",
          "command": "$HOME/Code/entropy-meter/entropy_hook.py"
        }]
      }
    ],
    "Stop": [
      {
        "hooks": [{
          "type": "command",
          "command": "$HOME/Code/entropy-meter/entropy_hook.py"
        }]
      }
    ]
  }
}
```

3. Add `*.db` to `.gitignore` (if not already present).

Measurements are stored project-locally at `.claude/entropy.db` — each project gets its own DB automatically.

### Uninstall

Remove the `hooks` entries from `.claude/settings.local.json`. Optionally delete `.claude/entropy.db` and run `uv pip uninstall entropy-meter`.

### Files

| File | Purpose |
|------|---------|
| [`entropy_hook.py`](entropy_hook.py) | Canonical hook script — referenced by all projects |
| [`examples/hooks_settings.json`](examples/hooks_settings.json) | Settings snippet to merge into `.claude/settings.local.json` |

## Data Storage

Measurements are stored in `.claude/entropy.db` (project-local, gitignored). SQLite with WAL mode, 0600 permissions.

## Configuration

Per-project via `pyproject.toml`:

```toml
[tool.entropy-meter]
exclude = ["migrations/**", "vendor/**"]
extensions = [".py"]

[tool.entropy-meter.thresholds]
warn = 65
alert = 80
```

## Development

```bash
make install    # Install with all extras + dev deps
make test       # Run pytest
make check      # Ruff lint + format + mypy
make test-cov   # Tests with 80% coverage minimum
```
