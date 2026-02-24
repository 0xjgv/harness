"""Tier 0 + Tier 1 metric computation for a single file.

Tier 0 uses only the stdlib.
Tier 1 uses radon for cyclomatic complexity, maintainability, and Halstead metrics.
"""

from __future__ import annotations

import gzip
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from radon.complexity import cc_visit
from radon.metrics import h_visit, mi_visit

from entropy_meter.config import TIER_0, TIER_1

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class FileMetrics:
    """Raw metrics for a single file."""

    # Tier 0
    file_size_bytes: int
    line_count: int
    blank_lines: int
    comment_lines: int
    compression_ratio: float
    line_length_stddev: float
    line_entropy: float
    # Tier 1
    cyclomatic_complexity: float | None = None
    maintainability_index: float | None = None
    halstead_volume: float | None = None
    # Tier 2 (reserved for future)
    ast_node_count: int | None = None
    ast_depth_max: int | None = None
    ast_entropy: float | None = None
    # Tier info
    tier_mask: int = TIER_0


def measure_file(path: Path | None = None, content: str | None = None) -> FileMetrics:
    """Measure all available metrics for a file.

    If content is provided, use it directly (for measuring git blob content).
    Otherwise read from path.
    """
    if content is None:
        if path is None:
            raise ValueError("Either 'path' or 'content' must be provided.")
        content = path.read_text(encoding="utf-8", errors="replace")

    content_bytes = content.encode("utf-8")

    # --- Tier 0: always available ---
    file_size_bytes = len(content_bytes)
    lines = content.splitlines()
    line_count = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())
    comment_lines = sum(1 for line in lines if line.strip().startswith("#"))
    compression_ratio = _compression_ratio(content_bytes)
    line_length_stddev = _line_length_stddev(lines)
    line_entropy = _shannon_entropy(content_bytes)

    # --- Tier 1: radon ---
    cyclomatic_complexity = _avg_cyclomatic(content)
    maintainability_index = _maintainability(content)
    halstead_volume = _halstead_volume(content)

    return FileMetrics(
        file_size_bytes=file_size_bytes,
        line_count=line_count,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        compression_ratio=compression_ratio,
        line_length_stddev=line_length_stddev,
        line_entropy=line_entropy,
        cyclomatic_complexity=cyclomatic_complexity,
        maintainability_index=maintainability_index,
        halstead_volume=halstead_volume,
        tier_mask=TIER_0 | TIER_1,
    )


# ---------------------------------------------------------------------------
# Tier 0 helpers
# ---------------------------------------------------------------------------


def _compression_ratio(data: bytes) -> float:
    """Return 1 - (compressed_size / original_size). Higher = more redundant."""
    if not data:
        return 0.0
    compressed = gzip.compress(data)
    return 1.0 - (len(compressed) / len(data))


def _line_length_stddev(lines: list[str]) -> float:
    """Population standard deviation of line lengths."""
    if len(lines) <= 1:
        return 0.0
    lengths = [len(line) for line in lines]
    return statistics.pstdev(lengths)


def _shannon_entropy(data: bytes) -> float:
    """Shannon entropy of byte-value distribution (bits per byte)."""
    if not data:
        return 0.0
    length = len(data)
    counts = Counter(data)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


# ---------------------------------------------------------------------------
# Tier 1 helpers (radon)
# ---------------------------------------------------------------------------


def _avg_cyclomatic(source: str) -> float:
    """Average per-function cyclomatic complexity via radon."""
    blocks = cc_visit(source)
    if not blocks:
        return 0.0
    return sum(b.complexity for b in blocks) / len(blocks)


def _maintainability(source: str) -> float:
    """Maintainability Index via radon."""
    return mi_visit(source, multi=True)


def _halstead_volume(source: str) -> float:
    """Sum of Halstead volumes via radon."""
    report = h_visit(source)
    if not report:
        return 0.0
    total = 0.0
    # h_visit returns a list of HalsteadReport objects (one per function)
    # or a single object for the whole module depending on version.
    if isinstance(report, list):
        for item in report:
            vol = getattr(item, "volume", None)
            if isinstance(vol, (int, float)):
                total += float(vol)
    elif hasattr(report, "total") and report.total is not None:
        vol = getattr(report.total, "volume", None)
        if isinstance(vol, (int, float)):
            total = float(vol)
    else:
        vol = getattr(report, "volume", None)
        if isinstance(vol, (int, float)):
            total = float(vol)
    return total
