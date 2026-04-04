"""
tests/test_create_student_invite_from_payload.py

Unit tests for execution/leads/create_student_invite_from_payload.py.
Uses an isolated database (tmp/test_create_invite.db) and never touches
the application database (tmp/app.db).
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                    # noqa: E402
from execution.leads.upsert_lead import upsert_lead                                 # noqa: E402
from execution.leads.create_student_invite_from_payload import (                    # noqa: E402
    create_student_invite_from_payload,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_create_invite.db")


class TestCreateStudentInviteFromPayload(unittest.TestCase):

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
    # Test 1 — creates lead when it does not already exist
    # ------------------------------------------------------------------
    def test_creates_lead_if_missing(self):
        """A lead row must be inserted when the lead_id is new."""
        create_student_invite_from_payload(
            "L1",
            name="Alice",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id, name FROM leads WHERE id = ?", ("L1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Lead L1 must exist after call")
        self.assertEqual(row["name"], "Alice")

    # ------------------------------------------------------------------
    # Test 2 — reuses existing lead without raising
    # ------------------------------------------------------------------
    def test_reuses_existing_lead(self):
        """Calling with an existing lead_id must not raise and must update name."""
        upsert_lead("L1", name="OldName", db_path=TEST_DB_PATH)

        result = create_student_invite_from_payload(
            "L1",
            name="NewName",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        self.assertEqual(result["lead_id"], "L1")

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT name FROM leads WHERE id = ?", ("L1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["name"], "NewName")

    # ------------------------------------------------------------------
    # Test 3 — enrollment row is created
    # ------------------------------------------------------------------
    def test_creates_enrollment(self):
        """An enrollment row for (lead_id, course_id) must exist after the call."""
        create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id FROM course_enrollments WHERE lead_id = ? AND course_id = ?",
                ("L1", "FREE_INTRO_AI_V0"),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Enrollment must exist after call")

    # ------------------------------------------------------------------
    # Test 4 — returned dict contains all expected keys
    # ------------------------------------------------------------------
    def test_returns_all_required_keys(self):
        """The returned dict must contain lead_id, course_id, enrollment_id,
        invite_id, token, and invite_link."""
        result = create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        for key in ("lead_id", "course_id", "enrollment_id", "invite_id", "token", "invite_link"):
            self.assertIn(key, result, f"Key '{key}' missing from result")

    # ------------------------------------------------------------------
    # Test 5 — returned values are correct
    # ------------------------------------------------------------------
    def test_returned_values_are_correct(self):
        """lead_id, course_id, enrollment_id, and invite_id must echo the inputs."""
        result = create_student_invite_from_payload(
            "L1",
            course_id="FREE_INTRO_AI_V0",
            invite_id="INV1",
            base_url="http://localhost:8501",
            db_path=TEST_DB_PATH,
        )

        self.assertEqual(result["lead_id"], "L1")
        self.assertEqual(result["course_id"], "FREE_INTRO_AI_V0")
        self.assertEqual(result["enrollment_id"], "ENR_L1_FREE_INTRO_AI_V0")
        self.assertEqual(result["invite_id"], "INV1")

    # ------------------------------------------------------------------
    # Test 6 — token is non-empty and invite_link contains the token
    # ------------------------------------------------------------------
    def test_token_and_invite_link(self):
        """token must be a non-empty string; invite_link must contain it."""
        result = create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            base_url="http://localhost:8501",
            db_path=TEST_DB_PATH,
        )

        self.assertIsInstance(result["token"], str)
        self.assertTrue(result["token"], "token must be non-empty")
        self.assertIn(result["token"], result["invite_link"])
        self.assertTrue(
            result["invite_link"].startswith("http://localhost:8501"),
            "invite_link must use the supplied base_url",
        )

    # ------------------------------------------------------------------
    # Test 7 — explicit course_id is stored and echoed
    # ------------------------------------------------------------------
    def test_explicit_course_id(self):
        """An explicit course_id must be stored in enrollment and invite, and echoed."""
        result = create_student_invite_from_payload(
            "L1",
            course_id="OTHER_COURSE_V1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        self.assertEqual(result["course_id"], "OTHER_COURSE_V1")
        self.assertEqual(result["enrollment_id"], "ENR_L1_OTHER_COURSE_V1")

        conn = connect(TEST_DB_PATH)
        try:
            enr = conn.execute(
                "SELECT course_id FROM course_enrollments WHERE id = ?",
                ("ENR_L1_OTHER_COURSE_V1",),
            ).fetchone()
            inv = conn.execute(
                "SELECT course_id FROM course_invites WHERE id = ?",
                ("INV1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(enr, "Enrollment for OTHER_COURSE_V1 must exist")
        self.assertEqual(enr["course_id"], "OTHER_COURSE_V1")
        self.assertIsNotNone(inv, "Invite INV1 must exist")
        self.assertEqual(inv["course_id"], "OTHER_COURSE_V1")

    # ------------------------------------------------------------------
    # Test 8 — idempotency: same invite_id returns the same token
    # ------------------------------------------------------------------
    def test_idempotent_same_invite_id_returns_same_token(self):
        """Calling twice with the same invite_id must return the same token
        without raising and without inserting a second invite row."""
        first = create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )
        second = create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        self.assertEqual(first["token"], second["token"])
        self.assertEqual(first["invite_link"], second["invite_link"])

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_invites WHERE id = ?", ("INV1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1, "Exactly one invite row must exist after two calls")

    # ------------------------------------------------------------------
    # Test 9 — auto-generated invite_id when none supplied
    # ------------------------------------------------------------------
    def test_auto_generates_invite_id(self):
        """When invite_id is omitted a new one must be generated and returned."""
        result = create_student_invite_from_payload(
            "L1",
            db_path=TEST_DB_PATH,
        )

        self.assertIsNotNone(result["invite_id"])
        self.assertTrue(result["invite_id"], "invite_id must be non-empty")

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT id FROM course_invites WHERE id = ?", (result["invite_id"],)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Invite row must exist for the auto-generated invite_id")

    # ------------------------------------------------------------------
    # Test 10 — invalid lead_id raises ValueError
    # ------------------------------------------------------------------
    def test_invalid_lead_id_raises_value_error(self):
        """An empty or non-string lead_id must raise ValueError immediately."""
        with self.assertRaises(ValueError):
            create_student_invite_from_payload("", db_path=TEST_DB_PATH)

        with self.assertRaises(ValueError):
            create_student_invite_from_payload("   ", db_path=TEST_DB_PATH)

        with self.assertRaises(ValueError):
            create_student_invite_from_payload(None, db_path=TEST_DB_PATH)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Test 11 — generation does not set sent_at (invite generated != sent)
    # ------------------------------------------------------------------
    def test_generation_does_not_set_sent_at(self):
        """After create_student_invite_from_payload the invite row must have
        sent_at = NULL.  Generating the link is not the same as sending it."""
        create_student_invite_from_payload(
            "L1",
            invite_id="INV1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT sent_at FROM course_invites WHERE id = ?", ("INV1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Invite row must exist after generation")
        self.assertIsNone(
            row["sent_at"],
            "sent_at must be NULL after generation — invite generated != invite sent",
        )


if __name__ == "__main__":
    unittest.main()
