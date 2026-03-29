"""Smoke test — verifies the package is importable."""

import unittest

import src


class TestSmoke(unittest.TestCase):
    def test_package_importable(self) -> None:
        self.assertIsNotNone(src)
