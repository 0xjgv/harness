"""Smoke test — verifies the package is importable."""

import src


def test_package_importable() -> None:
    assert src is not None
