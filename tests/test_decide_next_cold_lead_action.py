"""
tests/test_decide_next_cold_lead_action.py

Unit tests for execution/decision/decide_next_cold_lead_action.py.
Uses an isolated database (tmp/test_decision.db) and never touches
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

from execution.db.sqlite import connect, init_db                                        # noqa: E402
from execution.leads.upsert_lead import upsert_lead                                     # noqa: E402
from execution.leads.mark_course_invite_sent import mark_course_invite_sent             # noqa: E402
from execution.progress.record_progress_event import record_progress_event              # noqa: E402
from execution.progress.compute_course_state import compute_course_state                # noqa: E402
from execution.decision.decide_next_cold_lead_action import decide_next_cold_lead_action  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_decision.db")


class TestDecideNextColdLeadAction(unittest.TestCase):

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
    # Test 1 — lead does not exist
    # ------------------------------------------------------------------
    def test_no_lead(self):
        """Must return NO_LEAD when the lead_id is not in the database."""
        action = decide_next_cold_lead_action("MISSING", db_path=TEST_DB_PATH)
        self.assertEqual(action, "NO_LEAD")

    # ------------------------------------------------------------------
    # Test 2 — lead exists but no invite sent
    # ------------------------------------------------------------------
    def test_send_invite_when_no_invite(self):
        """Must return SEND_INVITE when the lead exists but has no course invite."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        action = decide_next_cold_lead_action("L1", db_path=TEST_DB_PATH)
        self.assertEqual(action, "SEND_INVITE")

    # ------------------------------------------------------------------
    # Test 3 — invite sent but no progress recorded
    # ------------------------------------------------------------------
    def test_nudge_start_class_when_invite_sent_but_no_progress(self):
        """Must return NUDGE_START_CLASS when invite sent but course not started."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        action = decide_next_cold_lead_action("L1", db_path=TEST_DB_PATH)
        self.assertEqual(action, "NUDGE_START_CLASS")

    # ------------------------------------------------------------------
    # Test 4 — started but not complete
    # ------------------------------------------------------------------
    def test_nudge_progress_when_started_not_complete(self):
        """Must return NUDGE_PROGRESS when course has started but is not 100% complete."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)
        action = decide_next_cold_lead_action("L1", db_path=TEST_DB_PATH)
        self.assertEqual(action, "NUDGE_PROGRESS")

    # ------------------------------------------------------------------
    # Test 5 — course complete (100%)
    # ------------------------------------------------------------------
    def test_ready_for_booking_when_complete(self):
        """Must return READY_FOR_BOOKING when completion_pct reaches 100."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=1, db_path=TEST_DB_PATH)  # 1/1 = 100%
        action = decide_next_cold_lead_action("L1", db_path=TEST_DB_PATH)
        self.assertEqual(action, "READY_FOR_BOOKING")


if __name__ == "__main__":
    unittest.main()
