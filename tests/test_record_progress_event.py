"""
tests/test_record_progress_event.py

Unit tests for execution/progress/record_progress_event.py.
Uses an isolated database (tmp/test_progress.db) and never touches
the application database (tmp/app.db).
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                          # noqa: E402
from execution.leads.upsert_lead import upsert_lead                       # noqa: E402
from execution.progress.record_progress_event import record_progress_event  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_progress.db")


class TestRecordProgressEvent(unittest.TestCase):

    def setUp(self):
        """Ensure tmp/ exists and the schema is initialised before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        """Remove the isolated test database after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 1 — successful insert
    # ------------------------------------------------------------------
    def test_insert_progress_event_success(self):
        """A progress event row must be created with the correct fields."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "section_1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, lead_id, section FROM progress_events WHERE id = ?",
                ("E1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected one row in progress_events but found none")
        self.assertEqual(row["id"], "E1")
        self.assertEqual(row["lead_id"], "L1")
        self.assertEqual(row["section"], "section_1")

    # ------------------------------------------------------------------
    # Test 2 — idempotency on duplicate event_id
    # ------------------------------------------------------------------
    def test_idempotent_duplicate_event(self):
        """Calling record_progress_event twice with the same event_id must insert only one row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "section_1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "section_1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM progress_events WHERE id = ?", ("E1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1, "Duplicate event_id must not create a second row")

    # ------------------------------------------------------------------
    # Test 3 — foreign key violation when lead is missing
    # ------------------------------------------------------------------
    def test_foreign_key_violation_when_lead_missing(self):
        """Inserting a progress event for a non-existent lead must raise IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            record_progress_event("E1", "MISSING_LEAD", "section_1", db_path=TEST_DB_PATH)


if __name__ == "__main__":
    unittest.main()
