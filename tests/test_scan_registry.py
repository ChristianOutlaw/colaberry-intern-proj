"""
tests/test_scan_registry.py

Unit tests for execution/scans/scan_registry.py.
Pure constants — no DB, no filesystem, no network.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.scans.scan_registry import (
    COMPLETION_FINALIZATION_SCAN,
    FAILED_DISPATCH_RETRY_SCAN,
    NO_START_SCAN,
    SCAN_NAMES,
    STALE_PROGRESS_SCAN,
    UNSENT_INVITE_SCAN,
    is_known_scan_name,
)


class TestScanRegistry(unittest.TestCase):

    def test_t1_known_names_return_true(self):
        """T1: each registered scan name returns True from is_known_scan_name."""
        self.assertTrue(is_known_scan_name(UNSENT_INVITE_SCAN))
        self.assertTrue(is_known_scan_name(NO_START_SCAN))
        self.assertTrue(is_known_scan_name(FAILED_DISPATCH_RETRY_SCAN))
        self.assertTrue(is_known_scan_name(STALE_PROGRESS_SCAN))
        self.assertTrue(is_known_scan_name(COMPLETION_FINALIZATION_SCAN))

    def test_t2_unknown_name_returns_false(self):
        """T2: an unregistered name returns False."""
        self.assertFalse(is_known_scan_name("NONEXISTENT_SCAN"))
        self.assertFalse(is_known_scan_name(""))

    def test_t3_registry_contains_exactly_implemented_scans(self):
        """T3: SCAN_NAMES contains exactly the five currently implemented scans."""
        expected = {
            "UNSENT_INVITE_SCAN",
            "NO_START_SCAN",
            "FAILED_DISPATCH_RETRY_SCAN",
            "STALE_PROGRESS_SCAN",
            "COMPLETION_FINALIZATION_SCAN",
        }
        self.assertEqual(SCAN_NAMES, expected)


if __name__ == "__main__":
    unittest.main()
