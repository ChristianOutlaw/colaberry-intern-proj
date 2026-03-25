"""
tests/test_map_scan_to_intended_action.py

Unit tests for execution/scans/map_scan_to_intended_action.py.
Pure function — no DB, no fixtures.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.scans.map_scan_to_intended_action import map_scan_to_intended_action


class TestMapScanToIntendedAction(unittest.TestCase):

    def test_unsent_invite_scan_maps_to_send_invite(self):
        self.assertEqual(map_scan_to_intended_action("UNSENT_INVITE_SCAN"), "SEND_INVITE")

    def test_no_start_scan_maps_to_nudge_progress(self):
        self.assertEqual(map_scan_to_intended_action("NO_START_SCAN"), "NUDGE_PROGRESS")

    def test_failed_dispatch_retry_scan_maps_to_requeue(self):
        self.assertEqual(map_scan_to_intended_action("FAILED_DISPATCH_RETRY_SCAN"), "REQUEUE_FAILED_ACTION")

    def test_stale_progress_scan_maps_to_nudge_progress(self):
        self.assertEqual(map_scan_to_intended_action("STALE_PROGRESS_SCAN"), "NUDGE_PROGRESS")

    def test_unknown_scan_name_returns_none(self):
        self.assertIsNone(map_scan_to_intended_action("UNKNOWN_SCAN"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(map_scan_to_intended_action(""))


if __name__ == "__main__":
    unittest.main()
