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
REPO_ROOT = Path(__file__).resolve().parents[2]
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

    # ------------------------------------------------------------------
    # Test 4 — token is generated and persisted automatically
    # ------------------------------------------------------------------
    def test_token_is_generated_and_persisted(self):
        """A non-empty token must be stored in course_invites.token on insert."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row["token"], "token must not be NULL after insert")
        self.assertGreater(len(row["token"]), 10, "token must be a non-trivially short string")

    # ------------------------------------------------------------------
    # Test 5 — each invite gets a distinct token
    # ------------------------------------------------------------------
    def test_tokens_are_unique_per_invite(self):
        """Two separate invites for different invite IDs must receive different tokens."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I2", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT token FROM course_invites WHERE id IN ('I1', 'I2')"
            ).fetchall()
        finally:
            conn.close()

        tokens = [r["token"] for r in rows]
        self.assertEqual(len(tokens), 2, "Expected two invite rows")
        self.assertNotEqual(tokens[0], tokens[1], "Each invite must have a unique token")

    # ------------------------------------------------------------------
    # Test 6 — course_id defaults to FREE_INTRO_AI_V0
    # ------------------------------------------------------------------
    def test_course_id_defaults_to_free_intro_ai_v0(self):
        """course_id must default to FREE_INTRO_AI_V0 when not supplied."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT course_id FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["course_id"], "FREE_INTRO_AI_V0",
                         "course_id must default to FREE_INTRO_AI_V0")

    # ------------------------------------------------------------------
    # Test 7 — explicit course_id is stored correctly
    # ------------------------------------------------------------------
    def test_explicit_course_id_stored(self):
        """An explicit course_id must be written to the invite row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            "I1", "L1",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT course_id FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["course_id"], "OTHER_COURSE_V1")

    # ------------------------------------------------------------------
    # Test 8 — creating an invite also creates a matching enrollment row
    # ------------------------------------------------------------------
    def test_invite_creates_matching_enrollment(self):
        """mark_course_invite_sent must ensure a course_enrollments row exists
        for the same (lead_id, course_id) after the invite is created."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT lead_id, course_id FROM course_enrollments "
                "WHERE lead_id = ? AND course_id = ?",
                ("L1", "FREE_INTRO_AI_V0"),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected a course_enrollments row after invite creation")
        self.assertEqual(row["lead_id"], "L1")
        self.assertEqual(row["course_id"], "FREE_INTRO_AI_V0")

    # ------------------------------------------------------------------
    # Test 9 — explicit course_id produces enrollment for that course
    # ------------------------------------------------------------------
    def test_invite_creates_enrollment_for_explicit_course_id(self):
        """An invite with an explicit course_id must create an enrollment for that course."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", course_id="OTHER_COURSE_V1", db_path=TEST_DB_PATH)

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
    # Test 10 — idempotent invite does not duplicate the enrollment row
    # ------------------------------------------------------------------
    def test_idempotent_invite_does_not_duplicate_enrollment(self):
        """Calling mark_course_invite_sent twice with the same invite_id must
        not create duplicate enrollment rows."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_enrollments WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1, "Duplicate invite must not create a second enrollment row")

    # ------------------------------------------------------------------
    # Test 11 — marks an existing generated row (sent_at=NULL) as sent
    # ------------------------------------------------------------------
    def test_marks_existing_generated_row_as_sent(self):
        """mark_course_invite_sent must UPDATE a generated row (sent_at=NULL) rather
        than inserting a duplicate.  This covers the path where
        create_student_invite_from_payload created the row first."""
        upsert_lead("L1", db_path=TEST_DB_PATH)

        # Simulate a generated (not yet sent) invite row: insert with sent_at = NULL.
        conn = connect(TEST_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO course_invites (id, lead_id, course_id, token) "
                "VALUES (?, ?, ?, ?)",
                ("I1", "L1", "FREE_INTRO_AI_V0", "tok-existing"),
            )
            conn.commit()
        finally:
            conn.close()

        # Now record delivery.
        mark_course_invite_sent(
            "I1", "L1",
            sent_at="2026-03-24T10:00:00+00:00",
            channel="email",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()[0]
            row = conn.execute(
                "SELECT sent_at, channel, token FROM course_invites WHERE id = ?",
                ("I1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(count, 1, "Must not create a duplicate row")
        self.assertEqual(row["sent_at"], "2026-03-24T10:00:00+00:00",
                         "sent_at must be set after mark_course_invite_sent")
        self.assertEqual(row["channel"], "email")
        self.assertEqual(row["token"], "tok-existing",
                         "token must be preserved from the generated row")


if __name__ == "__main__":
    unittest.main()
