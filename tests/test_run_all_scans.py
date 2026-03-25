"""
tests/test_run_all_scans.py

Unit tests for services/worker/run_all_scans.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.run_all_scans import run_all_scans

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_all_scans.db")

_EXPECTED_SCAN_NAMES = [
    "UNSENT_INVITE_SCAN",
    "NO_START_SCAN",
    "FAILED_DISPATCH_RETRY_SCAN",
    "STALE_PROGRESS_SCAN",
]


class TestRunAllScans(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — empty DB: shape and zero counts
    # ------------------------------------------------------------------
    def test_t1_empty_db_shape(self):
        """T1: empty DB → scan_count=4, limit_used=100, results has 4 items."""
        result = run_all_scans(db_path=TEST_DB_PATH)
        self.assertEqual(result["scan_count"], 4)
        self.assertEqual(result["limit_used"], 100)
        self.assertEqual(len(result["results"]), 4)
        self.assertIn("generated_at", result)
        self.assertIsInstance(result["generated_at"], str)
        self.assertEqual(result["action_summary"], {
            "SEND_INVITE":           1,
            "NUDGE_PROGRESS":        2,
            "REQUEUE_FAILED_ACTION": 1,
            "UNKNOWN":               0,
        })
        for scan in result["results"]:
            self.assertIn("scan_name", scan)
            self.assertIn("count", scan)
            self.assertEqual(scan["count"], 0)
            self.assertIn("intended_action", scan)

    # ------------------------------------------------------------------
    # T2 — custom limit propagates to all nested results
    # ------------------------------------------------------------------
    def test_t2_custom_limit_propagates(self):
        """T2: limit=5 → top-level limit_used=5 and each nested limit_used=5."""
        result = run_all_scans(limit=5, db_path=TEST_DB_PATH)
        self.assertEqual(result["limit_used"], 5)
        for scan in result["results"]:
            self.assertEqual(scan["limit_used"], 5)

    def test_t2b_generated_at_is_parseable_utc(self):
        """T2b: generated_at parses as a UTC ISO-8601 timestamp."""
        result = run_all_scans(db_path=TEST_DB_PATH)
        ts = datetime.fromisoformat(result["generated_at"].replace("Z", "+00:00"))
        self.assertEqual(ts.tzinfo, timezone.utc)

    # ------------------------------------------------------------------
    # T3 — scan_name order is fixed
    # ------------------------------------------------------------------
    def test_t3_scan_name_order(self):
        """T3: results appear in the canonical fixed order with correct intended_actions."""
        result = run_all_scans(db_path=TEST_DB_PATH)
        actual_names = [scan["scan_name"] for scan in result["results"]]
        self.assertEqual(actual_names, _EXPECTED_SCAN_NAMES)
        actual_actions = [scan["intended_action"] for scan in result["results"]]
        self.assertEqual(actual_actions, [
            "SEND_INVITE",
            "NUDGE_PROGRESS",
            "REQUEUE_FAILED_ACTION",
            "NUDGE_PROGRESS",
        ])
        self.assertEqual(
            set(result["action_summary"].keys()),
            {"SEND_INVITE", "NUDGE_PROGRESS", "REQUEUE_FAILED_ACTION", "UNKNOWN"},
        )


if __name__ == "__main__":
    unittest.main()
