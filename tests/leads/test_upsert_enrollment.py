"""
tests/test_upsert_enrollment.py

Unit tests for execution/leads/upsert_enrollment.py.
Uses an isolated database (tmp/test_enrollment.db) and never touches
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
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                       # noqa: E402
from execution.leads.upsert_lead import upsert_lead                    # noqa: E402
from execution.leads.upsert_enrollment import upsert_enrollment        # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_enrollment.db")


class TestUpsertEnrollment(unittest.TestCase):

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
    # Test 1 — successful insert with correct field values
    # ------------------------------------------------------------------
    def test_insert_enrollment_success(self):
        """A new enrollment row must be created with all required fields."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        result = upsert_enrollment("L1", db_path=TEST_DB_PATH)

        self.assertIsNotNone(result)
        self.assertEqual(result["lead_id"], "L1")
        self.assertEqual(result["course_id"], "FREE_INTRO_AI_V0")
        self.assertEqual(result["status"], "active")
        self.assertIsNotNone(result["id"])
        self.assertIsNotNone(result["created_at"])
        self.assertIsNotNone(result["updated_at"])

    # ------------------------------------------------------------------
    # Test 2 — stable enrollment ID derived from lead + course
    # ------------------------------------------------------------------
    def test_enrollment_id_is_stable_and_predictable(self):
        """Enrollment ID must encode lead_id and course_id."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        result = upsert_enrollment("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)

        self.assertIn("L1", result["id"])
        self.assertIn("FREE_INTRO_AI_V0", result["id"])

    # ------------------------------------------------------------------
    # Test 3 — idempotency: second call returns same row, no second insert
    # ------------------------------------------------------------------
    def test_idempotent_second_call_same_course(self):
        """Calling upsert_enrollment twice for the same (lead, course) must not
        create a second row and must return the same enrollment id."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        first = upsert_enrollment("L1", db_path=TEST_DB_PATH)
        second = upsert_enrollment("L1", db_path=TEST_DB_PATH)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["created_at"], second["created_at"])

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_enrollments WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1, "Duplicate enrollment must not create a second row")

    # ------------------------------------------------------------------
    # Test 4 — FK violation when lead does not exist
    # ------------------------------------------------------------------
    def test_foreign_key_violation_when_lead_missing(self):
        """Enrolling a non-existent lead must raise IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            upsert_enrollment("MISSING_LEAD", db_path=TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 5 — same lead can enroll in a different course
    # ------------------------------------------------------------------
    def test_same_lead_different_course_creates_second_row(self):
        """A lead enrolled in two courses must have two distinct enrollment rows."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        e1 = upsert_enrollment("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        e2 = upsert_enrollment("L1", "OTHER_COURSE_V1", db_path=TEST_DB_PATH)

        self.assertNotEqual(e1["id"], e2["id"])
        self.assertEqual(e1["course_id"], "FREE_INTRO_AI_V0")
        self.assertEqual(e2["course_id"], "OTHER_COURSE_V1")

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_enrollments WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 2)

    # ------------------------------------------------------------------
    # Test 6 — enrolled_at stored when supplied
    # ------------------------------------------------------------------
    def test_enrolled_at_stored_when_supplied(self):
        """An explicit enrolled_at timestamp must be persisted."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        ts = "2026-01-15T10:00:00+00:00"
        result = upsert_enrollment("L1", enrolled_at=ts, db_path=TEST_DB_PATH)

        self.assertEqual(result["enrolled_at"], ts)

    # ------------------------------------------------------------------
    # Test 7 — enrolled_at is NULL when omitted
    # ------------------------------------------------------------------
    def test_enrolled_at_null_when_omitted(self):
        """enrolled_at must be NULL when not supplied."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        result = upsert_enrollment("L1", db_path=TEST_DB_PATH)

        self.assertIsNone(result["enrolled_at"])

    # ------------------------------------------------------------------
    # Test 8 — explicit status is persisted
    # ------------------------------------------------------------------
    def test_explicit_status_stored(self):
        """A non-default status value must be written to the row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        result = upsert_enrollment("L1", status="completed", db_path=TEST_DB_PATH)

        self.assertEqual(result["status"], "completed")

    # ------------------------------------------------------------------
    # Test 9 — returned dict has all expected keys
    # ------------------------------------------------------------------
    def test_returned_dict_has_all_keys(self):
        """The returned dict must contain all course_enrollments columns."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        result = upsert_enrollment("L1", db_path=TEST_DB_PATH)

        expected_keys = {"id", "lead_id", "course_id", "enrolled_at",
                         "status", "created_at", "updated_at"}
        self.assertEqual(set(result.keys()), expected_keys)

    # ------------------------------------------------------------------
    # Test 10 — two different leads each get their own enrollment row
    # ------------------------------------------------------------------
    def test_different_leads_same_course_get_separate_rows(self):
        """Two leads enrolled in the same course must have distinct enrollment rows."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        upsert_lead("L2", db_path=TEST_DB_PATH)
        e1 = upsert_enrollment("L1", db_path=TEST_DB_PATH)
        e2 = upsert_enrollment("L2", db_path=TEST_DB_PATH)

        self.assertNotEqual(e1["id"], e2["id"])
        self.assertEqual(e1["course_id"], e2["course_id"])


if __name__ == "__main__":
    unittest.main()
