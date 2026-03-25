"""
tests/test_failed_scan_requeue_integration.py

Integration test: proves the scan → requeue boundary works end-to-end.

Flow:
  1. FAILED record appears in run_failed_dispatch_scan
  2. requeue_failed_action moves it to NEEDS_SYNC
  3. run_failed_dispatch_scan no longer returns it

One test, one isolated DB, no mocks.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.events.requeue_failed_action import requeue_failed_action
from services.worker.run_failed_dispatch_scan import run_failed_dispatch_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_failed_scan_requeue_integration.db")

_TS = "2026-01-01T00:00:00Z"


def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000", _TS, _TS),
    )
    conn.commit()
    conn.close()


def _seed_sync_record(lead_id: str, destination: str, status: str) -> int:
    conn = connect(TEST_DB_PATH)
    cur = conn.execute(
        "INSERT INTO sync_records"
        " (lead_id, destination, status, reason, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, destination, status, "TEST_REASON", _TS, _TS),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


class TestFailedScanRequeueIntegration(unittest.TestCase):

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

    def test_failed_record_scan_requeue_then_gone(self):
        """
        Integration: FAILED record appears in scan → requeue transitions it →
        scan no longer returns it.
        """
        # 1. Seed
        _seed_lead("lead-a")
        record_id = _seed_sync_record("lead-a", "CORY_BOOKING", "FAILED")

        # 2. Scan finds it
        scan_before = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        self.assertEqual(scan_before["count"], 1)
        self.assertIn(record_id, scan_before["record_ids"])

        # 3. Requeue transitions FAILED → NEEDS_SYNC
        requeue_result = requeue_failed_action(record_id, db_path=TEST_DB_PATH)
        self.assertTrue(requeue_result["updated"])
        self.assertEqual(requeue_result["new_status"], "NEEDS_SYNC")

        # 4. Scan no longer finds it
        scan_after = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        self.assertEqual(scan_after["count"], 0)
        self.assertEqual(scan_after["record_ids"], [])


if __name__ == "__main__":
    unittest.main()
