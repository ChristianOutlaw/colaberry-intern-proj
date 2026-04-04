"""
tests/test_requeue_failed_action.py

Unit tests for execution/events/requeue_failed_action.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.events.requeue_failed_action import requeue_failed_action

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_requeue_failed_action.db")

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


def _get_status(record_id: int) -> str | None:
    conn = connect(TEST_DB_PATH)
    row = conn.execute(
        "SELECT status FROM sync_records WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()
    return row["status"] if row else None


class TestRequeueFailedAction(unittest.TestCase):

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
    # T1 — FAILED record transitions to NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_t1_failed_record_becomes_needs_sync(self):
        """T1: FAILED record → updated=True, status becomes NEEDS_SYNC."""
        _seed_lead("lead-a")
        row_id = _seed_sync_record("lead-a", "CORY_BOOKING", "FAILED")

        result = requeue_failed_action(row_id, db_path=TEST_DB_PATH)

        self.assertTrue(result["updated"])
        self.assertEqual(result["previous_status"], "FAILED")
        self.assertEqual(result["new_status"], "NEEDS_SYNC")
        self.assertEqual(_get_status(row_id), "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T2 — NEEDS_SYNC record is unchanged
    # ------------------------------------------------------------------
    def test_t2_needs_sync_record_unchanged(self):
        """T2: NEEDS_SYNC record → updated=False, status unchanged."""
        _seed_lead("lead-b")
        row_id = _seed_sync_record("lead-b", "CORY_INVITE", "NEEDS_SYNC")

        result = requeue_failed_action(row_id, db_path=TEST_DB_PATH)

        self.assertFalse(result["updated"])
        self.assertEqual(result["current_status"], "NEEDS_SYNC")
        self.assertEqual(_get_status(row_id), "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T3 — SENT record is unchanged
    # ------------------------------------------------------------------
    def test_t3_sent_record_unchanged(self):
        """T3: SENT record → updated=False, status unchanged."""
        _seed_lead("lead-c")
        row_id = _seed_sync_record("lead-c", "CORY_NUDGE", "SENT")

        result = requeue_failed_action(row_id, db_path=TEST_DB_PATH)

        self.assertFalse(result["updated"])
        self.assertEqual(result["current_status"], "SENT")
        self.assertEqual(_get_status(row_id), "SENT")

    # ------------------------------------------------------------------
    # T4 — missing record id returns updated=False
    # ------------------------------------------------------------------
    def test_t4_missing_record_returns_not_found(self):
        """T4: non-existent record_id → updated=False, reason=NOT_FOUND."""
        result = requeue_failed_action(99999, db_path=TEST_DB_PATH)

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
