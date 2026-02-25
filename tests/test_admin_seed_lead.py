"""
tests/test_admin_seed_lead.py

Unit tests for execution/admin/seed_lead.py.

Covers:
    T1 — New lead, no invite → lead row exists, no course_invites row, message "created"
    T2 — mark_invite_sent=True with invite_id → invite row created, message "Invite recorded"
    T3 — Called twice with same lead_id → idempotent; no duplicate invite row; second message "updated"
    T4 — Empty lead_id → ok=False, no DB write
    T5 — mark_invite_sent=True but invite_id missing → raises ValueError before any write

Uses an isolated database (tmp/test_admin_seed_lead.db) and never touches
the application database (tmp/app.db).
All fixture data uses deterministic, hard-coded IDs.  No randomness.
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.admin.seed_lead import seed_lead        # noqa: E402
from execution.db.sqlite import connect, init_db       # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_admin_seed_lead.db")

# ---------------------------------------------------------------------------
# Fixture constants — deterministic IDs, never changed between test runs.
# ---------------------------------------------------------------------------
LEAD_ID   = "SEED_LEAD_01"
INVITE_ID = "SEED_INVITE_01"
TIMESTAMP = "2026-01-20T09:00:00+00:00"


def _lead_row(lead_id: str) -> dict | None:
    """Return the leads row as a plain dict, or None if not found."""
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _invite_count(lead_id: str) -> int:
    """Return the number of course_invites rows for lead_id."""
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM course_invites WHERE lead_id = ?", (lead_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _lead_count() -> int:
    """Return total number of rows in the leads table."""
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    finally:
        conn.close()


class TestSeedLead(unittest.TestCase):

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
    # T1 — New lead, no invite
    # ------------------------------------------------------------------
    def test_new_lead_no_invite_creates_lead_row(self):
        """A new lead must be created with no course_invites row; message says 'created'."""
        result = seed_lead(
            lead_id=LEAD_ID,
            name="Test User",
            phone="555-0100",
            email="test@example.com",
            db_path=TEST_DB_PATH,
        )

        self.assertTrue(result["ok"])
        self.assertIn("created", result["message"])
        self.assertIn(LEAD_ID, result["message"])

        # Lead row must exist with the supplied fields.
        row = _lead_row(LEAD_ID)
        self.assertIsNotNone(row, "Lead row must be present after seed_lead")
        self.assertEqual(row["name"], "Test User")
        self.assertEqual(row["phone"], "555-0100")
        self.assertEqual(row["email"], "test@example.com")

        # No invite row must have been created.
        self.assertEqual(
            _invite_count(LEAD_ID), 0,
            "No course_invites row must exist when mark_invite_sent=False",
        )

    # ------------------------------------------------------------------
    # T2 — mark_invite_sent=True with invite_id
    # ------------------------------------------------------------------
    def test_new_lead_with_invite_creates_both_rows(self):
        """mark_invite_sent=True must create both a lead row and a course_invites row."""
        result = seed_lead(
            lead_id=LEAD_ID,
            mark_invite_sent=True,
            invite_id=INVITE_ID,
            sent_at=TIMESTAMP,
            channel="sms",
            db_path=TEST_DB_PATH,
        )

        self.assertTrue(result["ok"])
        self.assertIn("created", result["message"])
        self.assertIn("Invite recorded", result["message"])

        self.assertIsNotNone(_lead_row(LEAD_ID), "Lead row must exist")
        self.assertEqual(
            _invite_count(LEAD_ID), 1,
            "Exactly one course_invites row must be created",
        )

        # Confirm the invite row carries the supplied values.
        conn = connect(TEST_DB_PATH)
        try:
            invite_row = conn.execute(
                "SELECT id, lead_id, sent_at, channel FROM course_invites WHERE id = ?",
                (INVITE_ID,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(invite_row)
        self.assertEqual(invite_row["id"], INVITE_ID)
        self.assertEqual(invite_row["lead_id"], LEAD_ID)
        self.assertEqual(invite_row["sent_at"], TIMESTAMP)
        self.assertEqual(invite_row["channel"], "sms")

    # ------------------------------------------------------------------
    # T3 — Idempotency: called twice with same lead_id
    # ------------------------------------------------------------------
    def test_idempotent_second_call_updates_lead_and_does_not_duplicate_invite(self):
        """A second seed_lead call must update the lead (not duplicate it) and not duplicate the invite."""
        # First call — creates lead and invite.
        first = seed_lead(
            lead_id=LEAD_ID,
            name="Original",
            mark_invite_sent=True,
            invite_id=INVITE_ID,
            sent_at=TIMESTAMP,
            channel="email",
            db_path=TEST_DB_PATH,
        )
        self.assertIn("created", first["message"])

        # Second call — same lead_id and invite_id.
        second = seed_lead(
            lead_id=LEAD_ID,
            name="Updated Name",
            mark_invite_sent=True,
            invite_id=INVITE_ID,
            sent_at=TIMESTAMP,
            channel="email",
            db_path=TEST_DB_PATH,
        )

        self.assertTrue(second["ok"])
        self.assertIn("updated", second["message"])
        self.assertIn("Invite recorded", second["message"])

        # Still only one lead row.
        self.assertEqual(_lead_count(), 1, "No duplicate lead row must be created")

        # Still only one invite row.
        self.assertEqual(
            _invite_count(LEAD_ID), 1,
            "No duplicate course_invites row must be created on second call",
        )

        # Name must reflect the second call's value.
        row = _lead_row(LEAD_ID)
        self.assertEqual(row["name"], "Updated Name")

    # ------------------------------------------------------------------
    # T4 — Empty lead_id returns ok=False without writing
    # ------------------------------------------------------------------
    def test_empty_lead_id_returns_ok_false_without_writing(self):
        """A blank lead_id must return ok=False and write nothing to the DB."""
        result = seed_lead(lead_id="   ", db_path=TEST_DB_PATH)

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Lead ID is required.")
        self.assertEqual(_lead_count(), 0, "No lead row must be written for empty lead_id")

    # ------------------------------------------------------------------
    # T5 — mark_invite_sent=True but invite_id missing raises ValueError
    # ------------------------------------------------------------------
    def test_missing_invite_id_raises_value_error_before_any_write(self):
        """Omitting invite_id when mark_invite_sent=True must raise ValueError before any DB write."""
        with self.assertRaises(ValueError) as ctx:
            seed_lead(
                lead_id=LEAD_ID,
                mark_invite_sent=True,
                invite_id=None,
                db_path=TEST_DB_PATH,
            )

        self.assertIn("invite_id", str(ctx.exception))

        # No lead row must have been written.
        self.assertEqual(
            _lead_count(), 0,
            "No lead row must be written when ValueError is raised for missing invite_id",
        )

    # ------------------------------------------------------------------
    # Extra — optional fields (name/phone/email) default to None correctly
    # ------------------------------------------------------------------
    def test_minimal_call_with_only_lead_id(self):
        """seed_lead must succeed with only lead_id supplied; optional fields default to None."""
        result = seed_lead(lead_id=LEAD_ID, db_path=TEST_DB_PATH)

        self.assertTrue(result["ok"])
        row = _lead_row(LEAD_ID)
        self.assertIsNotNone(row)
        self.assertIsNone(row["name"])
        self.assertIsNone(row["phone"])
        self.assertIsNone(row["email"])


if __name__ == "__main__":
    unittest.main()
