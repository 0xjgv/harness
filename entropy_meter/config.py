"""Shared constants, defaults, weight vectors, and DB path resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

# --- Tier definitions ---
TIER_0 = 0b001
TIER_1 = 0b010
TIER_2 = 0b100

# --- Default weight vectors ---
# Keys map to metric names; values are weights that sum to 1.0.
# Grouped by tier for redistribution when a tier is unavailable.
DEFAULT_WEIGHTS: dict[str, tuple[float, int]] = {
    # (weight, tier_bit)
    "compression_ratio": (0.25, TIER_0),
    "line_entropy": (0.15, TIER_0),
    "cyclomatic": (0.20, TIER_1),
    "maintainability": (0.15, TIER_1),
    "halstead_volume": (0.10, TIER_1),
    "ast_entropy": (0.15, TIER_2),
}

# --- Normalization ceilings (raw value → 1.0) ---
METRIC_CEILINGS: dict[str, float] = {
    "compression_ratio": 1.0,  # 0.0 (incompressible) to 1.0 (fully redundant)
    "line_entropy": 5.0,  # bits per character, Shannon entropy
    "cyclomatic": 30.0,  # per-function average
    "maintainability": 100.0,  # radon MI (inverted: higher MI = less complex)
    "halstead_volume": 5000.0,  # typical ceiling for a single file
    "ast_entropy": 4.0,  # Shannon entropy of AST node-type distribution
}

# --- Feedback thresholds ---
DEFAULT_WARN_THRESHOLD = 65
DEFAULT_ALERT_THRESHOLD = 80

# --- Asymmetric neutral band for delta feedback ---
DELTA_POSITIVE_FLOOR = -5.0  # Below this: positive reinforcement
DELTA_NEUTRAL_CEILING = 2.0  # Above this: informational
DELTA_SUGGESTION_CEILING = 10.0  # Above this: actionable suggestion
DELTA_WARNING_CEILING = 25.0  # Above this: warning

# --- File filtering ---
DEFAULT_EXTENSIONS = frozenset({".py"})
DEFAULT_EXCLUDES = frozenset({"migrations/**", "vendor/**", "**/generated_*.py"})


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from `start` to find the nearest directory containing .git or pyproject.toml."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / ".git").exists() or (directory / "pyproject.toml").exists():
            return directory
    return current


def get_db_path(project_root: Path | None = None) -> Path:
    """Return the path to the project-local entropy DB: <project>/.claude/entropy.db."""
    root = project_root or find_project_root()
    db_dir = root / ".claude"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "entropy.db"


def get_project_config(project_root: Path | None = None) -> dict[str, object]:
    """Read [tool.entropy-meter] from pyproject.toml if present."""
    root = project_root or find_project_root()
    toml_path = root / "pyproject.toml"
    if not toml_path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        # Python 3.10 compatibility
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    return dict(data.get("tool", {}).get("entropy-meter", {}))


def get_current_commit() -> str | None:
    """Return current HEAD commit hash, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
