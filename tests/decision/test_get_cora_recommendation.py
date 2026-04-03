"""
tests/test_get_cora_recommendation.py

Unit tests for execution/decision/get_cora_recommendation.py.

Uses an isolated database (tmp/test_get_cora_recommendation.db) and never
touches the application database (tmp/app.db).

All tests inject a fixed reference datetime (_NOW) — no test ever calls
datetime.now() directly.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                          # noqa: E402
from execution.leads.upsert_lead import upsert_lead                                       # noqa: E402
from execution.leads.create_student_invite_from_payload import create_student_invite_from_payload  # noqa: E402
from execution.leads.mark_course_invite_sent import mark_course_invite_sent               # noqa: E402
from execution.progress.record_progress_event import record_progress_event                # noqa: E402
from execution.progress.compute_course_state import compute_course_state                  # noqa: E402
from execution.decision.get_cora_recommendation import get_cora_recommendation            # noqa: E402

# Fixed reference time used by all tests (matches repo convention).
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_get_cora_recommendation.db")


class TestGetCoraRecommendation(unittest.TestCase):

    def setUp(self):
        """Ensure tmp/ exists and schema is initialised before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        """Remove the isolated test database after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_count(self, table: str) -> int:
        conn = connect(TEST_DB_PATH)
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        conn.close()
        return count

    # ------------------------------------------------------------------
    # T1 — lead exists, no invite sent → SEND_INVITE
    # ------------------------------------------------------------------
    def test_send_invite_when_no_invite(self):
        upsert_lead("L1", db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(rec["event_type"], "SEND_INVITE")
        self.assertEqual(rec["priority"], "LOW")
        self.assertEqual(rec["recommended_channel"], "EMAIL")
        self.assertIn("NOT_INVITED", rec["reason_codes"])

    # ------------------------------------------------------------------
    # T2 — invite sent, course not started → NUDGE_PROGRESS (INVITED_NO_START)
    # ------------------------------------------------------------------
    def test_nudge_progress_when_invited_not_started(self):
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(rec["event_type"], "NUDGE_PROGRESS")
        self.assertEqual(rec["priority"], "MEDIUM")
        self.assertEqual(rec["recommended_channel"], "EMAIL")
        self.assertIn("INVITED_NO_START", rec["reason_codes"])

    # ------------------------------------------------------------------
    # T3 — hot signal active (invite + ≥25% + activity within 7 days)
    #      → HOT_LEAD_BOOKING
    # ------------------------------------------------------------------
    def test_hot_lead_booking_when_hot(self):
        # Activity 3 days before _NOW → within the 7-day HOT window.
        activity_ts = "2026-02-22T12:00:00+00:00"

        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1", occurred_at=activity_ts, db_path=TEST_DB_PATH
        )
        record_progress_event(
            "E2", "L1", "P1_S2", occurred_at=activity_ts, db_path=TEST_DB_PATH
        )
        # 2 distinct sections / 2 total = 100% → satisfies Rule 2 completion gate.
        compute_course_state("L1", total_sections=2, db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(rec["event_type"], "READY_FOR_BOOKING")
        self.assertEqual(rec["priority"], "HIGH")
        self.assertEqual(rec["recommended_channel"], "CALL")

    # ------------------------------------------------------------------
    # T4 — in progress, recently active, not hot (completion < 25%)
    #      → NUDGE_PROGRESS
    # ------------------------------------------------------------------
    def test_nudge_progress_when_active_not_hot(self):
        # Activity 5 days before _NOW: recently active but below 25% threshold.
        activity_ts = "2026-02-20T12:00:00+00:00"

        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1", occurred_at=activity_ts, db_path=TEST_DB_PATH
        )
        # 1 of 9 sections = 11.1% — below the 25% HOT completion gate.
        compute_course_state("L1", total_sections=9, db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(rec["event_type"], "NUDGE_PROGRESS")
        self.assertEqual(rec["priority"], "MEDIUM")
        self.assertEqual(rec["recommended_channel"], "EMAIL")

    # ------------------------------------------------------------------
    # T5 — course 100% complete, not hot (stale activity) → NO_ACTION
    # ------------------------------------------------------------------
    def test_no_action_when_complete(self):
        # Activity 55 days before _NOW → well outside the 7-day HOT window.
        activity_ts = "2026-01-01T12:00:00+00:00"

        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1", occurred_at=activity_ts, db_path=TEST_DB_PATH
        )
        # 1 of 1 total_sections = 100%.
        compute_course_state("L1", total_sections=1, db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(rec["event_type"], "REENGAGE_COMPLETED")
        self.assertEqual(rec["priority"], "MEDIUM")
        self.assertEqual(rec["recommended_channel"], "EMAIL")

    # ------------------------------------------------------------------
    # T6 — non-existent lead raises ValueError
    # ------------------------------------------------------------------
    def test_non_existent_lead_raises(self):
        with self.assertRaises(ValueError):
            get_cora_recommendation("MISSING", now=_NOW, db_path=TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T7 — read-only: calling the function does not write to the DB
    # ------------------------------------------------------------------
    def test_read_only_does_not_write_to_db(self):
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        # Capture row counts in all affected tables before the call.
        counts_before = {
            t: self._row_count(t)
            for t in ("leads", "course_invites", "progress_events",
                      "course_state", "hot_lead_signals", "sync_records",
                      "reflection_responses")
        }

        get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        counts_after = {
            t: self._row_count(t)
            for t in counts_before
        }

        self.assertEqual(
            counts_before, counts_after,
            "get_cora_recommendation must not write to the database",
        )

    # ------------------------------------------------------------------
    # T8 — output shape is complete and valid; temperature fields populated
    # ------------------------------------------------------------------
    def test_output_shape_is_complete(self):
        upsert_lead("L1", db_path=TEST_DB_PATH)

        rec = get_cora_recommendation("L1", now=_NOW, db_path=TEST_DB_PATH)

        required_keys = {
            "lead_id", "event_type", "priority", "reason_codes",
            "recommended_channel", "payload", "status", "built_at",
        }
        self.assertEqual(required_keys, set(rec.keys()))
        self.assertEqual(rec["lead_id"], "L1")
        self.assertEqual(rec["status"], "READY")
        self.assertIsInstance(rec["reason_codes"], list)
        self.assertIsInstance(rec["payload"], dict)

        # Temperature fields must be populated — never None.
        payload = rec["payload"]
        self.assertIn(
            payload["temperature_signal"], {"HOT", "WARM", "COLD"},
            "temperature_signal must be HOT, WARM, or COLD",
        )
        self.assertIsInstance(payload["temperature_score"], int)
        self.assertGreaterEqual(payload["temperature_score"], 0)
        self.assertLessEqual(payload["temperature_score"], 100)

        # upstream_reason_codes must include temperature component codes.
        # QUIZ_UNKNOWN is always present when avg_quiz_score is None (not yet
        # instrumented), making it a reliable sentinel for temperature codes.
        self.assertIn(
            "QUIZ_UNKNOWN", payload["upstream_reason_codes"],
            "upstream_reason_codes must include temperature component codes",
        )


    # ------------------------------------------------------------------
    # T9 — Regression: invite generated (sent_at IS NULL) != invite sent
    #
    # Scenario A: invite row exists but sent_at IS NULL → SEND_INVITE
    # Scenario B: same invite after mark_course_invite_sent → NUDGE_PROGRESS
    #
    # This pins the business rule from spec §5:
    #   "invite generated is NOT the same as invite sent"
    #   "get_lead_status keys invite_sent off sent_at IS NOT NULL"
    # ------------------------------------------------------------------
    def test_generated_invite_not_sent_still_returns_send_invite(self):
        """Scenario A: invite row exists with sent_at IS NULL → SEND_INVITE, not NUDGE_PROGRESS."""
        # create_student_invite_from_payload inserts an invite row with sent_at = NULL.
        # mark_course_invite_sent is deliberately NOT called.
        create_student_invite_from_payload(
            lead_id="L9",
            invite_id="INV9",
            db_path=TEST_DB_PATH,
        )

        rec = get_cora_recommendation("L9", now=_NOW, db_path=TEST_DB_PATH)

        # Prove the invite row exists but has sent_at = NULL.
        conn = connect(TEST_DB_PATH)
        row = conn.execute(
            "SELECT sent_at FROM course_invites WHERE id = ?", ("INV9",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row, "invite row must exist")
        self.assertIsNone(row["sent_at"], "sent_at must be NULL — invite generated, not sent")

        # Recommendation must treat this as uninvited.
        self.assertEqual(
            rec["event_type"], "SEND_INVITE",
            "invite generated (sent_at IS NULL) must still produce SEND_INVITE",
        )
        self.assertIn("NOT_INVITED", rec["reason_codes"])

    def test_after_mark_sent_transitions_away_from_send_invite(self):
        """Scenario B: same invite after mark_course_invite_sent → no longer SEND_INVITE."""
        create_student_invite_from_payload(
            lead_id="L9b",
            invite_id="INV9b",
            db_path=TEST_DB_PATH,
        )

        # Confirm SEND_INVITE before marking sent (Scenario A state).
        rec_before = get_cora_recommendation("L9b", now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(rec_before["event_type"], "SEND_INVITE")

        # Now mark the invite as sent.
        mark_course_invite_sent("INV9b", "L9b", db_path=TEST_DB_PATH)

        # Prove sent_at is now populated.
        conn = connect(TEST_DB_PATH)
        row = conn.execute(
            "SELECT sent_at FROM course_invites WHERE id = ?", ("INV9b",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row["sent_at"], "sent_at must be set after mark_course_invite_sent")

        # Recommendation must no longer be SEND_INVITE.
        rec_after = get_cora_recommendation("L9b", now=_NOW, db_path=TEST_DB_PATH)
        self.assertNotEqual(
            rec_after["event_type"], "SEND_INVITE",
            "after mark_course_invite_sent, event_type must not be SEND_INVITE",
        )
        # Invited but course not started → NUDGE_PROGRESS.
        self.assertEqual(rec_after["event_type"], "NUDGE_PROGRESS")
        self.assertIn("INVITED_NO_START", rec_after["reason_codes"])


if __name__ == "__main__":
    unittest.main()
