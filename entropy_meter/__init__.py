"""entropy-meter: Code complexity metrics with graceful degradation."""

__version__ = "0.1.0"

from entropy_meter.config import get_db_path
from entropy_meter.core.composite import compute_entropy_index
from entropy_meter.core.metrics import measure_file

__all__ = ["__version__", "compute_entropy_index", "get_db_path", "measure_file"]
