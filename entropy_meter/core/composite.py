"""Entropy Index aggregation: computes a 0-100 composite score from FileMetrics.

Weights from unavailable tiers are redistributed proportionally among
available tiers so the score is always comparable regardless of which
optional dependencies are installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from entropy_meter.config import DEFAULT_WEIGHTS, METRIC_CEILINGS, TIER_0, TIER_1, TIER_2
from entropy_meter.core.metrics import FileMetrics, measure_file

if TYPE_CHECKING:
    from pathlib import Path

# Map metric names (as used in DEFAULT_WEIGHTS) to FileMetrics field names.
_METRIC_FIELD_MAP: dict[str, str] = {
    "compression_ratio": "compression_ratio",
    "line_entropy": "line_entropy",
    "cyclomatic": "cyclomatic_complexity",
    "maintainability": "maintainability_index",
    "halstead_volume": "halstead_volume",
    "ast_entropy": "ast_entropy",
}

# Tiers associated with each bit position.
_TIER_BITS = (TIER_0, TIER_1, TIER_2)


def compute_entropy_index(metrics: FileMetrics) -> float:
    """Compute the Entropy Index (0-100) from raw file metrics.

    Weights from unavailable tiers are redistributed proportionally
    among available tiers.
    """
    # 1. Determine available tiers
    available_tiers = {bit for bit in _TIER_BITS if metrics.tier_mask & bit}

    # 2. Filter weights to only include metrics from available tiers
    available_weights: dict[str, float] = {}
    for metric_name, (weight, tier_bit) in DEFAULT_WEIGHTS.items():
        if tier_bit in available_tiers:
            available_weights[metric_name] = weight

    # 3. Redistribute: normalize remaining weights to sum to 1.0
    weight_sum = sum(available_weights.values())
    if weight_sum == 0:
        return 0.0

    normalized_weights: dict[str, float] = {
        name: w / weight_sum for name, w in available_weights.items()
    }

    # 4. Compute weighted normalized score
    total = 0.0
    for metric_name, norm_weight in normalized_weights.items():
        field_name = _METRIC_FIELD_MAP.get(metric_name)
        if field_name is None:
            continue

        raw_value = getattr(metrics, field_name, None)
        if raw_value is None:
            continue

        ceiling = METRIC_CEILINGS.get(metric_name, 1.0)

        # Special case: maintainability is inverted (higher MI = less complex)
        if metric_name == "maintainability":
            raw_value = 100.0 - raw_value

        # Normalize and clamp
        normalized = min(raw_value / ceiling, 1.0) if ceiling > 0 else 0.0

        total += normalized * norm_weight

    # 5. Scale to 0-100 and clamp
    result = total * 100.0
    result = max(0.0, min(100.0, result))
    return round(result, 1)


def measure_and_score(path: Path, content: str | None = None) -> tuple[FileMetrics, float]:
    """Measure a file and compute its entropy index in one call."""
    metrics = measure_file(path, content)
    ei = compute_entropy_index(metrics)
    return metrics, ei
