"""Shared fixtures for harness test suite."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from harness.core.db import get_connection


@pytest.fixture()
def tmp_db(tmp_path: Path):
    """Yield a temporary SQLite connection using core/db.py's get_connection."""
    db_path = tmp_path / "test_entropy.db"
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def sample_python_code() -> str:
    """A short, simple Python function for metric testing."""
    return textwrap.dedent("""\
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        if __name__ == "__main__":
            print(greet("world"))
    """)


@pytest.fixture()
def complex_python_code() -> str:
    """More complex Python code with nested ifs, loops, and multiple functions."""
    return textwrap.dedent("""\
        import math

        def classify(value: float) -> str:
            if value < 0:
                if value < -100:
                    return "extreme_negative"
                elif value < -10:
                    return "very_negative"
                else:
                    return "negative"
            elif value == 0:
                return "zero"
            else:
                if value > 100:
                    return "extreme_positive"
                elif value > 10:
                    return "very_positive"
                else:
                    return "positive"

        def process_batch(items: list[float]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for item in items:
                label = classify(item)
                if label in counts:
                    counts[label] += 1
                else:
                    counts[label] = 1
            return counts

        def compute_stats(data: list[float]) -> dict[str, float]:
            if not data:
                return {"mean": 0.0, "stddev": 0.0}
            mean = sum(data) / len(data)
            variance = 0.0
            for x in data:
                diff = x - mean
                variance += diff * diff
            variance /= len(data)
            stddev = math.sqrt(variance)
            return {"mean": mean, "stddev": stddev}

        class DataPipeline:
            def __init__(self, raw: list[float]) -> None:
                self.raw = raw
                self.processed: list[float] = []

            def filter_invalid(self) -> None:
                self.processed = [x for x in self.raw if not math.isnan(x)]

            def normalize(self) -> None:
                if not self.processed:
                    return
                lo = min(self.processed)
                hi = max(self.processed)
                rng = hi - lo
                if rng == 0:
                    self.processed = [0.0] * len(self.processed)
                else:
                    self.processed = [(x - lo) / rng for x in self.processed]

            def run(self) -> list[float]:
                self.filter_invalid()
                self.normalize()
                return self.processed
    """)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """A tmp_path that looks like a project (has .git dir and pyproject.toml)."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "test-project"\n')
    return tmp_path
