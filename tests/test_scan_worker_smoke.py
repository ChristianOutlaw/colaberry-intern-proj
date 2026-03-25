"""
tests/test_scan_worker_smoke.py

Smoke tests for all four scan worker wrappers.
Verifies consistent return shape and correct scan_name values on an empty DB.
Does not test selection behaviour — that lives in the wrapper-specific test files.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.scans.scan_registry import (
    FAILED_DISPATCH_RETRY_SCAN,
    NO_START_SCAN,
    STALE_PROGRESS_SCAN,
    UNSENT_INVITE_SCAN,
)
from services.worker.run_failed_dispatch_scan import run_failed_dispatch_scan
from services.worker.run_no_start_scan import run_no_start_scan
from services.worker.run_stale_progress_scan import run_stale_progress_scan
from services.worker.run_unsent_invite_scan import run_unsent_invite_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_scan_worker_smoke.db")


class TestScanWorkerSmoke(unittest.TestCase):

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

    def _all_results(self) -> list[dict]:
        return [
            run_unsent_invite_scan(db_path=TEST_DB_PATH),
            run_no_start_scan(db_path=TEST_DB_PATH),
            run_failed_dispatch_scan(db_path=TEST_DB_PATH),
            run_stale_progress_scan(db_path=TEST_DB_PATH),
        ]

    # ------------------------------------------------------------------
    # T1 — empty DB: all wrappers return valid shape with count == 0
    # ------------------------------------------------------------------
    def test_t1_empty_db_shape_and_zero_count(self):
        """T1: all four wrappers return a dict with scan_name and count=0 on empty DB."""
        for result in self._all_results():
            with self.subTest(scan=result.get("scan_name")):
                self.assertIsInstance(result, dict)
                self.assertIn("scan_name", result)
                self.assertIn("count", result)
                self.assertEqual(result["count"], 0)

    # ------------------------------------------------------------------
    # T2 — returned scan_name values match registry constants exactly
    # ------------------------------------------------------------------
    def test_t2_scan_names_match_registry(self):
        """T2: each wrapper's scan_name matches the canonical registry constant."""
        unsent  = run_unsent_invite_scan(db_path=TEST_DB_PATH)
        no_start = run_no_start_scan(db_path=TEST_DB_PATH)
        failed  = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        stale   = run_stale_progress_scan(db_path=TEST_DB_PATH)

        self.assertEqual(unsent["scan_name"],   UNSENT_INVITE_SCAN)
        self.assertEqual(no_start["scan_name"], NO_START_SCAN)
        self.assertEqual(failed["scan_name"],   FAILED_DISPATCH_RETRY_SCAN)
        self.assertEqual(stale["scan_name"],    STALE_PROGRESS_SCAN)


if __name__ == "__main__":
    unittest.main()
