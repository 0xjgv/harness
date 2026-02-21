"""Example module for testing entropy metrics.

Demonstrates a class with methods, control flow, and utility functions.
"""


class DataProcessor:
    """Processes a list of numeric data points."""

    def __init__(self, data: list[float]) -> None:
        self.data = data
        self._cache: dict[str, float] = {}

    def mean(self) -> float:
        """Calculate the arithmetic mean."""
        if not self.data:
            return 0.0
        return sum(self.data) / len(self.data)

    def median(self) -> float:
        """Calculate the median value."""
        if not self.data:
            return 0.0
        sorted_data = sorted(self.data)
        n = len(sorted_data)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2.0
        return sorted_data[mid]


def filter_outliers(values: list[float], threshold: float = 2.0) -> list[float]:
    """Remove values more than `threshold` standard deviations from the mean."""
    if len(values) < 2:
        return list(values)
    avg = sum(values) / len(values)
    variance = sum((x - avg) ** 2 for x in values) / len(values)
    stddev = variance**0.5
    # Keep values within the band
    return [v for v in values if abs(v - avg) <= threshold * stddev]
