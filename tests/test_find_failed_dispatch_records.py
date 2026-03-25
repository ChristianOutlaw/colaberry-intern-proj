"""
tests/test_find_failed_dispatch_records.py

Unit tests for execution/scans/find_failed_dispatch_records.py.
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
from execution.scans.find_failed_dispatch_records import find_failed_dispatch_records

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_failed_dispatch_records.db")

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


def _seed_sync_record(lead_id: str, destination: str, status: str, error: str | None = None) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO sync_records"
        " (lead_id, destination, status, reason, error, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lead_id, destination, status, "TEST_REASON", error, _TS, _TS),
    )
    conn.commit()
    conn.close()


class TestFindFailedDispatchRecords(unittest.TestCase):

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
    # T1 — empty DB → empty list
    # ------------------------------------------------------------------
    def test_t1_no_failed_records_returns_empty(self):
        """T1: no sync_records in DB → empty result."""
        result = find_failed_dispatch_records(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — FAILED record is returned
    # ------------------------------------------------------------------
    def test_t2_failed_record_is_returned(self):
        """T2: one FAILED sync_record → included in results."""
        _seed_lead("lead-a")
        _seed_sync_record("lead-a", "CORY_BOOKING", "FAILED", error="timeout")
        result = find_failed_dispatch_records(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-a")
        self.assertEqual(result[0]["status"], "FAILED")

    # ------------------------------------------------------------------
    # T3 — non-FAILED records are excluded
    # ------------------------------------------------------------------
    def test_t3_non_failed_records_excluded(self):
        """T3: NEEDS_SYNC and SENT records → excluded; only FAILED returned."""
        _seed_lead("lead-b")
        _seed_lead("lead-c")
        _seed_lead("lead-d")
        _seed_sync_record("lead-b", "CORY_INVITE",   "NEEDS_SYNC")
        _seed_sync_record("lead-c", "CORY_NUDGE",    "SENT")
        _seed_sync_record("lead-d", "CORY_BOOKING",  "FAILED", error="connection refused")
        result = find_failed_dispatch_records(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-d")

    # ------------------------------------------------------------------
    # T4 — limit is respected
    # ------------------------------------------------------------------
    def test_t4_limit_is_respected(self):
        """T4: three FAILED records, limit=2 → only 2 returned."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_sync_record(i, "CORY_BOOKING", "FAILED", error="timeout")
        result = find_failed_dispatch_records(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
