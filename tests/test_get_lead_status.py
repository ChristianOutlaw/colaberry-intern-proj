"""
tests/test_get_lead_status.py

Unit tests for execution/leads/get_lead_status.py.
Uses an isolated database (tmp/test_lead_status.db) and never touches
the application database (tmp/app.db).
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

from execution.db.sqlite import connect, init_db                            # noqa: E402
from execution.leads.upsert_lead import upsert_lead                         # noqa: E402
from execution.leads.get_lead_status import get_lead_status                 # noqa: E402
from execution.progress.record_progress_event import record_progress_event  # noqa: E402
from execution.progress.compute_course_state import compute_course_state    # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_lead_status.db")


class TestGetLeadStatus(unittest.TestCase):

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
    # Test 1 — missing lead returns empty status
    # ------------------------------------------------------------------
    def test_missing_lead_returns_empty_status(self):
        """get_lead_status must return the empty shape when the lead does not exist."""
        status = get_lead_status("MISSING", db_path=TEST_DB_PATH)

        self.assertFalse(status["lead_exists"])
        self.assertFalse(status["invite_sent"])
        self.assertIsNone(status["course_state"]["current_section"])
        self.assertIsNone(status["course_state"]["completion_pct"])
        self.assertIsNone(status["course_state"]["last_activity_at"])
        self.assertIsNone(status["hot_lead"]["signal"])
        self.assertIsNone(status["hot_lead"]["score"])
        self.assertIsNone(status["hot_lead"]["reason"])

    # ------------------------------------------------------------------
    # Test 2 — lead exists but no related rows
    # ------------------------------------------------------------------
    def test_lead_exists_no_related_rows(self):
        """A lead with no invites and no progress must return NOT_HOT / NOT_INVITED.

        Rule 1 of HotLeadSignal (directives/HOT_LEAD_SIGNAL.md) fires immediately
        when invite_sent is False, so signal and reason are always deterministic —
        never None — for a known lead.
        """
        upsert_lead("L1", db_path=TEST_DB_PATH)
        status = get_lead_status("L1", db_path=TEST_DB_PATH)

        self.assertTrue(status["lead_exists"])
        self.assertFalse(status["invite_sent"])
        self.assertIsNone(status["course_state"]["current_section"])
        self.assertIsNone(status["course_state"]["completion_pct"])
        self.assertIsNone(status["course_state"]["last_activity_at"])
        # HotLeadSignal Rule 1 fails (no invite) → always computable, never None.
        self.assertEqual(status["hot_lead"]["signal"], "NOT_HOT")
        self.assertIsNone(status["hot_lead"]["score"])
        self.assertEqual(status["hot_lead"]["reason"], "NOT_INVITED")

    # ------------------------------------------------------------------
    # Test 3 — invite_sent True when a course_invites row exists
    # ------------------------------------------------------------------
    def test_invite_sent_true_when_invite_row_exists(self):
        """invite_sent must be True when at least one course_invites row is present."""
        upsert_lead("L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO course_invites (id, lead_id, sent_at, channel, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("I1", "L1", "2026-01-01T00:00:00+00:00", "sms", None),
            )
            conn.commit()
        finally:
            conn.close()

        status = get_lead_status("L1", db_path=TEST_DB_PATH)
        self.assertTrue(status["invite_sent"])

    # ------------------------------------------------------------------
    # Test 4 — course_state fields populated when state row exists
    # ------------------------------------------------------------------
    def test_course_state_fields_returned(self):
        """course_state fields must reflect the computed state from progress events."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E2", "L1", "P1_S2",
            occurred_at="2026-01-02T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)

        status = get_lead_status("L1", db_path=TEST_DB_PATH)
        cs = status["course_state"]

        self.assertEqual(cs["current_section"], "P1_S2")
        self.assertAlmostEqual(cs["completion_pct"], 20.0, places=5)
        self.assertEqual(cs["last_activity_at"], "2026-01-02T00:00:00+00:00")

    # ------------------------------------------------------------------
    # Test 5 — hot_lead fields derived via compute-on-read HotLeadSignal
    # ------------------------------------------------------------------
    def test_hot_lead_fields_returned(self):
        """hot_lead fields must reflect the computed HotLeadSignal, not a stored row.

        A lead with an invite and a course_state that passes all three gates
        (completion >= 25 %, last activity within 7 days) must produce
        signal="HOT", score=None, reason="HOT_ENGAGED".
        """
        upsert_lead("L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO course_invites (id, lead_id, sent_at, channel, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("I1", "L1", "2026-02-20T12:00:00Z", "sms", None),
            )
            conn.execute(
                """
                INSERT INTO course_state
                    (lead_id, current_section, completion_pct, last_activity_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("L1", "section_3", 30.0, "2026-02-21T12:00:00Z", "2026-02-21T12:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        status = get_lead_status("L1", db_path=TEST_DB_PATH)
        hl = status["hot_lead"]

        self.assertEqual(hl["signal"], "HOT")
        self.assertIsNone(hl["score"])
        self.assertEqual(hl["reason"], "HOT_ENGAGED")


if __name__ == "__main__":
    unittest.main()
