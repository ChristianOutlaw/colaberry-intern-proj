"""
tests/test_classify_no_start_threshold.py

Unit tests for execution/scans/classify_no_start_threshold.py.
Pure function — no DB, no filesystem, no network.
All tests inject a fixed _NOW datetime for determinism.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.scans.classify_no_start_threshold import classify_no_start_threshold

_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    """Return an ISO-8601 UTC string for a timestamp N hours before _NOW."""
    ts = _NOW - timedelta(hours=hours_ago)
    return ts.isoformat().replace("+00:00", "Z")


class TestClassifyNoStartThreshold(unittest.TestCase):

    def test_t1_none_invite_returns_none(self):
        """T1: invite_sent_at is None → None."""
        self.assertIsNone(classify_no_start_threshold(None, _NOW))

    def test_t2_23_hours_returns_none(self):
        """T2: 23 hours elapsed → below 24h threshold → None."""
        self.assertIsNone(classify_no_start_threshold(_iso(23), _NOW))

    def test_t3_exactly_24_hours_returns_24h(self):
        """T3: exactly 24 hours elapsed → NO_START_24H."""
        self.assertEqual(classify_no_start_threshold(_iso(24), _NOW), "NO_START_24H")

    def test_t4_exactly_72_hours_returns_72h(self):
        """T4: exactly 72 hours elapsed → NO_START_72H."""
        self.assertEqual(classify_no_start_threshold(_iso(72), _NOW), "NO_START_72H")

    def test_t5_exactly_7_days_returns_7d(self):
        """T5: exactly 7 days (168 hours) elapsed → NO_START_7D."""
        self.assertEqual(classify_no_start_threshold(_iso(168), _NOW), "NO_START_7D")

    def test_t6_10_days_returns_7d(self):
        """T6: 10 days elapsed → NO_START_7D (highest bucket)."""
        self.assertEqual(classify_no_start_threshold(_iso(240), _NOW), "NO_START_7D")


if __name__ == "__main__":
    unittest.main()
