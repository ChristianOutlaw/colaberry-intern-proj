"""
tests/test_classify_stale_progress_threshold.py

Unit tests for execution/scans/classify_stale_progress_threshold.py.
Pure function — no DB, no filesystem, no network.
All tests inject a fixed _NOW datetime for determinism.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.scans.classify_stale_progress_threshold import classify_stale_progress_threshold

_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    """Return an ISO-8601 UTC string for a timestamp N hours before _NOW."""
    ts = _NOW - timedelta(hours=hours_ago)
    return ts.isoformat().replace("+00:00", "Z")


class TestClassifyStaleProgressThreshold(unittest.TestCase):

    def test_t1_none_activity_returns_none(self):
        """T1: last_activity_at is None → None."""
        self.assertIsNone(classify_stale_progress_threshold(None, _NOW))

    def test_t2_47_hours_returns_none(self):
        """T2: 47 hours elapsed → below 48h threshold → None."""
        self.assertIsNone(classify_stale_progress_threshold(_iso(47), _NOW))

    def test_t3_exactly_48_hours_returns_48h(self):
        """T3: exactly 48 hours elapsed → INACTIVE_48H."""
        self.assertEqual(classify_stale_progress_threshold(_iso(48), _NOW), "INACTIVE_48H")

    def test_t4_exactly_4_days_returns_4d(self):
        """T4: exactly 4 days (96 hours) elapsed → INACTIVE_4D."""
        self.assertEqual(classify_stale_progress_threshold(_iso(96), _NOW), "INACTIVE_4D")

    def test_t5_exactly_7_days_returns_7d(self):
        """T5: exactly 7 days (168 hours) elapsed → INACTIVE_7D."""
        self.assertEqual(classify_stale_progress_threshold(_iso(168), _NOW), "INACTIVE_7D")

    def test_t6_10_days_returns_7d(self):
        """T6: 10 days elapsed → INACTIVE_7D (highest bucket)."""
        self.assertEqual(classify_stale_progress_threshold(_iso(240), _NOW), "INACTIVE_7D")


if __name__ == "__main__":
    unittest.main()
