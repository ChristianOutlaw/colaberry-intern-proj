"""
tests/test_admin_reset_progress.py

Unit tests for execution/admin/reset_progress.py.

Covers:
    U5  — Lead with 3 progress events: ok=True, events_deleted==3, lead row intact
    U6  — reset_invite=True also clears course_invites rows
    U7  — confirm=False raises OperationNotConfirmedError; no rows deleted
    U8  — Lead does not exist: ok=False, nothing deleted
    +   — Empty lead_id returns ok=False without writing or raising

Uses an isolated database (tmp/test_admin_reset_progress.db) and never
touches the application database (tmp/app.db).
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

from execution.admin.reset_progress import (          # noqa: E402
    OperationNotConfirmedError,
    reset_progress,
)
from execution.db.sqlite import connect, init_db       # noqa: E402
from execution.leads.mark_course_invite_sent import (  # noqa: E402
    mark_course_invite_sent,
)
from execution.leads.upsert_lead import upsert_lead    # noqa: E402
from execution.progress.record_progress_event import ( # noqa: E402
    record_progress_event,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_admin_reset_progress.db")

# ---------------------------------------------------------------------------
# Fixture constants — deterministic IDs, never changed between test runs.
# ---------------------------------------------------------------------------
LEAD_ID   = "HARNESS_LEAD_01"
INVITE_ID = "HARNESS_INVITE_01"
SECTIONS  = ("P1_S1", "P1_S2", "P1_S3")
EVENTS    = [f"HARNESS_EVT_{s}" for s in SECTIONS]
TIMESTAMP = "2026-01-15T10:00:00+00:00"


def _count(table: str, lead_id: str) -> int:
    """Return the number of rows in *table* whose lead_id matches."""
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE lead_id = ?",  # noqa: S608
            (lead_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def _lead_exists(lead_id: str) -> bool:
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


class TestResetProgress(unittest.TestCase):

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
    # Arrange helper — seed lead + invite + N progress events.
    # ------------------------------------------------------------------
    def _seed_full(self):
        """Insert LEAD_ID with one invite and three progress events."""
        upsert_lead(LEAD_ID, db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            INVITE_ID, LEAD_ID,
            sent_at=TIMESTAMP, channel="sms",
            db_path=TEST_DB_PATH,
        )
        for event_id, section in zip(EVENTS, SECTIONS):
            record_progress_event(
                event_id, LEAD_ID, section,
                occurred_at=TIMESTAMP,
                db_path=TEST_DB_PATH,
            )

    # ------------------------------------------------------------------
    # U7 — confirm=False raises OperationNotConfirmedError; no rows deleted
    # ------------------------------------------------------------------
    def test_confirm_false_raises_and_does_not_delete(self):
        """confirm=False must raise OperationNotConfirmedError before any write."""
        self._seed_full()

        with self.assertRaises(OperationNotConfirmedError) as ctx:
            reset_progress(lead_id=LEAD_ID, confirm=False, db_path=TEST_DB_PATH)

        self.assertIn("confirm=True", str(ctx.exception))

        # No progress_events rows must have been touched.
        self.assertEqual(
            _count("progress_events", LEAD_ID), 3,
            "progress_events rows must not be deleted when confirm=False",
        )
        # No course_invites rows must have been touched.
        self.assertEqual(
            _count("course_invites", LEAD_ID), 1,
            "course_invites rows must not be deleted when confirm=False",
        )

    # ------------------------------------------------------------------
    # U8 — Lead does not exist: ok=False, nothing deleted
    # ------------------------------------------------------------------
    def test_missing_lead_returns_ok_false(self):
        """When the lead does not exist, return ok=False with the expected message."""
        result = reset_progress(
            lead_id="NO_SUCH_LEAD", confirm=True, db_path=TEST_DB_PATH
        )

        self.assertFalse(result["ok"])
        self.assertIn("NO_SUCH_LEAD", result["message"])
        self.assertIn("not found", result["message"])

    # ------------------------------------------------------------------
    # U5 — Lead with 3 events: ok=True, events_deleted==3, lead row intact
    # ------------------------------------------------------------------
    def test_reset_clears_progress_events_and_preserves_lead_row(self):
        """reset_progress must delete all progress_events and leave the lead row."""
        self._seed_full()
        self.assertEqual(_count("progress_events", LEAD_ID), 3)

        result = reset_progress(
            lead_id=LEAD_ID, confirm=True, db_path=TEST_DB_PATH
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["events_deleted"], 3)
        self.assertFalse(result["invites_cleared"])
        self.assertIn("3 event(s) deleted", result["message"])

        # All progress_events rows must be gone.
        self.assertEqual(
            _count("progress_events", LEAD_ID), 0,
            "All progress_events rows must be deleted",
        )
        # Lead row must still exist.
        self.assertTrue(
            _lead_exists(LEAD_ID),
            "Lead row must not be deleted by reset_progress",
        )
        # Invite row must be untouched (reset_invite defaulted to False).
        self.assertEqual(
            _count("course_invites", LEAD_ID), 1,
            "course_invites must not be deleted when reset_invite=False",
        )

    # ------------------------------------------------------------------
    # U6 — reset_invite=True also clears course_invites rows
    # ------------------------------------------------------------------
    def test_reset_invite_true_clears_course_invites(self):
        """reset_invite=True must delete course_invites rows as well."""
        self._seed_full()

        result = reset_progress(
            lead_id=LEAD_ID,
            reset_invite=True,
            confirm=True,
            db_path=TEST_DB_PATH,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["events_deleted"], 3)
        self.assertTrue(result["invites_cleared"])
        self.assertIn("Invite record(s) cleared", result["message"])

        self.assertEqual(_count("progress_events", LEAD_ID), 0)
        self.assertEqual(_count("course_invites", LEAD_ID), 0)
        # Lead row must still exist.
        self.assertTrue(_lead_exists(LEAD_ID))

    # ------------------------------------------------------------------
    # Extra — empty lead_id returns ok=False without raising or writing
    # ------------------------------------------------------------------
    def test_empty_lead_id_returns_ok_false(self):
        """A blank lead_id must return ok=False with the required message."""
        result = reset_progress(lead_id="   ", confirm=True, db_path=TEST_DB_PATH)

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Lead ID is required.")

    # ------------------------------------------------------------------
    # Extra — zero events: ok=True, events_deleted==0
    # ------------------------------------------------------------------
    def test_lead_with_no_events_returns_events_deleted_zero(self):
        """A lead with no progress events must return ok=True, events_deleted=0."""
        upsert_lead(LEAD_ID, db_path=TEST_DB_PATH)

        result = reset_progress(
            lead_id=LEAD_ID, confirm=True, db_path=TEST_DB_PATH
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["events_deleted"], 0)
        self.assertIn("0 event(s) deleted", result["message"])


if __name__ == "__main__":
    unittest.main()
