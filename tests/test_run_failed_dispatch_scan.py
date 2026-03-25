"""
tests/test_run_failed_dispatch_scan.py

Unit tests for services/worker/run_failed_dispatch_scan.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.run_failed_dispatch_scan import run_failed_dispatch_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_failed_dispatch_scan.db")

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
    """Insert a sync_record and return its auto-assigned id."""
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


class TestRunFailedDispatchScan(unittest.TestCase):

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
    # T1 — empty DB → count 0, record_ids []
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_zero_count(self):
        """T1: no sync_records → count=0, record_ids=[]."""
        result = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["scan_name"], "FAILED_DISPATCH_RETRY_SCAN")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["record_ids"], [])

    # ------------------------------------------------------------------
    # T2 — one FAILED record → count 1 and correct record id
    # ------------------------------------------------------------------
    def test_t2_one_failed_record(self):
        """T2: one FAILED sync_record → count=1, correct id in record_ids."""
        _seed_lead("lead-a")
        row_id = _seed_sync_record("lead-a", "CORY_BOOKING", "FAILED")
        result = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn(row_id, result["record_ids"])

    # ------------------------------------------------------------------
    # T3 — non-FAILED records excluded
    # ------------------------------------------------------------------
    def test_t3_non_failed_records_excluded(self):
        """T3: NEEDS_SYNC and SENT records → excluded; only FAILED returned."""
        _seed_lead("lead-b")
        _seed_lead("lead-c")
        _seed_lead("lead-d")
        _seed_sync_record("lead-b", "CORY_INVITE",  "NEEDS_SYNC")
        _seed_sync_record("lead-c", "CORY_NUDGE",   "SENT")
        row_id = _seed_sync_record("lead-d", "CORY_BOOKING", "FAILED")
        result = run_failed_dispatch_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn(row_id, result["record_ids"])

    # ------------------------------------------------------------------
    # T4 — limit respected through the worker wrapper
    # ------------------------------------------------------------------
    def test_t4_limit_respected(self):
        """T4: three FAILED records, limit=2 → count=2."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_sync_record(i, "CORY_BOOKING", "FAILED")
        result = run_failed_dispatch_scan(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["record_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
