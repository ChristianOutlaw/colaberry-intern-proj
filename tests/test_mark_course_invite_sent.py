"""
tests/test_mark_course_invite_sent.py

Unit tests for execution/leads/mark_course_invite_sent.py.
Uses an isolated database (tmp/test_invites.db) and never touches
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
from execution.leads.mark_course_invite_sent import mark_course_invite_sent  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_invites.db")


class TestMarkCourseInviteSent(unittest.TestCase):

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
    def test_insert_invite_success(self):
        """An invite row must be created with the correct field values."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            "I1", "L1",
            sent_at="2026-01-01T00:00:00+00:00",
            channel="sms",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, lead_id, sent_at, channel FROM course_invites WHERE id = ?",
                ("I1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected one row in course_invites but found none")
        self.assertEqual(row["id"], "I1")
        self.assertEqual(row["lead_id"], "L1")
        self.assertEqual(row["sent_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(row["channel"], "sms")

    # ------------------------------------------------------------------
    # Test 2 — idempotency on duplicate invite_id
    # ------------------------------------------------------------------
    def test_idempotent_duplicate_invite_id(self):
        """Calling mark_course_invite_sent twice with the same invite_id must insert only one row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", channel="sms", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", channel="sms", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1, "Duplicate invite_id must not create a second row")

    # ------------------------------------------------------------------
    # Test 3 — foreign key violation when lead is missing
    # ------------------------------------------------------------------
    def test_foreign_key_violation_when_lead_missing(self):
        """Inserting an invite for a non-existent lead must raise IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            mark_course_invite_sent("I1", "MISSING", db_path=TEST_DB_PATH)


if __name__ == "__main__":
    unittest.main()
