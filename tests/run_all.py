"""Discover the full Python suite from either the repository or frontend directory."""

from __future__ import annotations

import pathlib
import sys
import unittest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPOSITORY_ROOT), str(REPOSITORY_ROOT / "backend")]

suite = unittest.defaultTestLoader.discover(
    start_dir=str(REPOSITORY_ROOT),
    pattern="test*.py",
    top_level_dir=str(REPOSITORY_ROOT),
)
test_count = suite.countTestCases()
if test_count == 0:
    raise SystemExit("Python test discovery found no tests; refusing a false-green result.")

result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
