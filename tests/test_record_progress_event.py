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

from execution.db.sqlite import connect, init_db                             # noqa: E402
from execution.leads.upsert_lead import upsert_lead                          # noqa: E402
from execution.progress.record_progress_event import record_progress_event   # noqa: E402

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
    # Test 1 — successful insert with a valid canonical section ID (AC1)
    # ------------------------------------------------------------------
    def test_insert_progress_event_success(self):
        """A progress event row must be created with the correct fields."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "P2_S2", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, lead_id, course_id, section FROM progress_events WHERE id = ?",
                ("E1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected one row in progress_events but found none")
        self.assertEqual(row["id"], "E1")
        self.assertEqual(row["lead_id"], "L1")
        self.assertEqual(row["section"], "P2_S2")
        self.assertEqual(row["course_id"], "FREE_INTRO_AI_V0",
                         "course_id must default to FREE_INTRO_AI_V0")

    # ------------------------------------------------------------------
    # Test 6 — explicit course_id is stored correctly
    # ------------------------------------------------------------------
    def test_explicit_course_id_stored(self):
        """An explicit course_id must be written to the row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT course_id FROM progress_events WHERE id = ?", ("E1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["course_id"], "OTHER_COURSE_V1")

    # ------------------------------------------------------------------
    # Test 2 — idempotency on duplicate event_id (AC3)
    # ------------------------------------------------------------------
    def test_idempotent_duplicate_event(self):
        """Calling record_progress_event twice with the same event_id must insert only one row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "P1_S1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "P1_S1", db_path=TEST_DB_PATH)

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
            record_progress_event("E1", "MISSING_LEAD", "P1_S1", db_path=TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 4 — invalid section_id raises ValueError before any DB write (AC2)
    # ------------------------------------------------------------------
    def test_invalid_section_id_raises_value_error(self):
        """An unknown section_id must raise ValueError with a message naming the bad value."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        with self.assertRaises(ValueError) as ctx:
            record_progress_event("E1", "L1", "PHASE_X_S99", db_path=TEST_DB_PATH)
        self.assertIn("Invalid section_id:", str(ctx.exception))
        self.assertIn("PHASE_X_S99", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 5 — invalid section_id leaves the DB unchanged (AC2)
    # ------------------------------------------------------------------
    def test_invalid_section_id_does_not_write_row(self):
        """No progress_events row must be written when the section_id is invalid."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        with self.assertRaises(ValueError):
            record_progress_event("E1", "L1", "PHASE_X_S99", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM progress_events WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 0, "No row must be written for an invalid section_id")

    # ------------------------------------------------------------------
    # Test 7 — recording progress creates a matching enrollment row
    # ------------------------------------------------------------------
    def test_progress_event_creates_matching_enrollment(self):
        """record_progress_event must ensure a course_enrollments row exists
        for the same (lead_id, course_id) after the event is recorded."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event("E1", "L1", "P1_S1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT lead_id, course_id FROM course_enrollments "
                "WHERE lead_id = ? AND course_id = ?",
                ("L1", "FREE_INTRO_AI_V0"),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected a course_enrollments row after progress event")
        self.assertEqual(row["lead_id"], "L1")
        self.assertEqual(row["course_id"], "FREE_INTRO_AI_V0")

    # ------------------------------------------------------------------
    # Test 8 — explicit course_id produces enrollment for that course
    # ------------------------------------------------------------------
    def test_progress_event_creates_enrollment_for_explicit_course_id(self):
        """An event with an explicit course_id must create an enrollment for that course."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT course_id FROM course_enrollments "
                "WHERE lead_id = ? AND course_id = ?",
                ("L1", "OTHER_COURSE_V1"),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected enrollment for OTHER_COURSE_V1")
        self.assertEqual(row["course_id"], "OTHER_COURSE_V1")

    # ------------------------------------------------------------------
    # Test 9 — invalid section does not create an enrollment row
    # ------------------------------------------------------------------
    def test_invalid_section_does_not_create_enrollment(self):
        """A ValueError from section validation must not leave an enrollment row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        with self.assertRaises(ValueError):
            record_progress_event("E1", "L1", "PHASE_X_S99", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_enrollments WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 0, "No enrollment must be created for an invalid section_id")


if __name__ == "__main__":
    unittest.main()
