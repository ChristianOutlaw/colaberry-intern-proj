"""
tests/test_consume_cory_recommendation.py

Unit tests for execution/events/consume_cory_recommendation.py.

All tests are fast, deterministic, and require no network access.
An isolated SQLite file is created and removed per test.
Inputs are fixed; datetime.now() is never called by the function under test
(it uses the payload's built_at field as the record timestamp).

Scenarios covered:
    T1  — HOT_LEAD_BOOKING HIGH  → writes CORY_BOOKING
    T2  — NUDGE_PROGRESS MEDIUM  → writes CORY_NUDGE
    T3  — SEND_INVITE HIGH       → writes CORY_INVITE
    T4  — REENGAGE_STALLED_LEAD MEDIUM → writes CORY_REENGAGE
    T5  — NUDGE_START_CLASS      → no write (log-only gate)
    T6  — NO_ACTION              → no write (ignore gate)
    T7  — LOW priority queueable → no write (LOW_PRIORITY gate)
    T8  — recommended_channel None → no write (NO_CHANNEL gate)
    T8b — recommended_channel "NONE" → no write (NO_CHANNEL gate)
    T9  — unknown event_type     → raises ValueError
    T10 — duplicate call         → no second NEEDS_SYNC row inserted
    T11 — Row content correct on first write
    T12 — Missing lead_id raises ValueError
    T13 — Lead not found in DB   → ok=False, reason=LEAD_NOT_FOUND
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                          # noqa: E402
from execution.events.consume_cory_recommendation import (                # noqa: E402
    consume_cory_recommendation,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_consume_cory_recommendation.db")

# ---------------------------------------------------------------------------
# Fixed fixture values — deterministic across all runs.
# ---------------------------------------------------------------------------
LEAD_ID = "CORY_CONSUMER_TEST_LEAD"
BUILT_AT = "2026-03-22T19:14:36.000000Z"

_BASE_PAYLOAD: dict = {
    "lead_id": LEAD_ID,
    "section": "P2_S1",
    "event_type": "HOT_LEAD_BOOKING",
    "priority": "HIGH",
    "recommended_channel": "EMAIL",
    "reason_codes": ["HOT_ENGAGED"],
    "built_at": BUILT_AT,
}


def _payload(**overrides) -> dict:
    """Return a copy of _BASE_PAYLOAD with selective field overrides."""
    p = dict(_BASE_PAYLOAD)
    p.update(overrides)
    return p


class TestConsumeCoryRecommendation(unittest.TestCase):

    def setUp(self):
        """Create an isolated DB with one test lead before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        # Insert the test lead so FK constraints are satisfied.
        conn.execute(
            """
            INSERT OR IGNORE INTO leads (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (LEAD_ID, "Test Lead", BUILT_AT, BUILT_AT),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        """Remove the isolated DB after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call(self, **overrides) -> dict:
        return consume_cory_recommendation(
            _payload(**overrides), db_path=TEST_DB_PATH
        )

    def _sync_rows(self, lead_id: str = LEAD_ID) -> list[dict]:
        conn = connect(TEST_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT * FROM sync_records WHERE lead_id = ?", (lead_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _sync_count(self, lead_id: str = LEAD_ID) -> int:
        return len(self._sync_rows(lead_id))

    # ------------------------------------------------------------------
    # T1 — HOT_LEAD_BOOKING HIGH → writes CORY_BOOKING
    # ------------------------------------------------------------------
    def test_hot_lead_booking_high_writes_cory_booking(self):
        result = self._call(event_type="HOT_LEAD_BOOKING", priority="HIGH")

        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote"])
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._sync_count(), 1)

    # ------------------------------------------------------------------
    # T2 — NUDGE_PROGRESS MEDIUM → writes CORY_NUDGE
    # ------------------------------------------------------------------
    def test_nudge_progress_medium_writes_cory_nudge(self):
        result = self._call(event_type="NUDGE_PROGRESS", priority="MEDIUM")

        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote"])
        self.assertEqual(result["destination"], "CORY_NUDGE")
        self.assertEqual(self._sync_count(), 1)

    # ------------------------------------------------------------------
    # T3 — SEND_INVITE HIGH → writes CORY_INVITE
    # ------------------------------------------------------------------
    def test_send_invite_high_writes_cory_invite(self):
        result = self._call(event_type="SEND_INVITE", priority="HIGH")

        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote"])
        self.assertEqual(result["destination"], "CORY_INVITE")
        self.assertEqual(self._sync_count(), 1)

    # ------------------------------------------------------------------
    # T4 — REENGAGE_STALLED_LEAD MEDIUM → writes CORY_REENGAGE
    # ------------------------------------------------------------------
    def test_reengage_stalled_lead_medium_writes_cory_reengage(self):
        result = self._call(event_type="REENGAGE_STALLED_LEAD", priority="MEDIUM")

        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote"])
        self.assertEqual(result["destination"], "CORY_REENGAGE")
        self.assertEqual(self._sync_count(), 1)

    # ------------------------------------------------------------------
    # T5 — NUDGE_START_CLASS → no write (log-only gate)
    # ------------------------------------------------------------------
    def test_nudge_start_class_writes_nothing(self):
        result = self._call(event_type="NUDGE_START_CLASS")

        self.assertTrue(result["ok"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["reason"], "NUDGE_START_CLASS")
        self.assertEqual(self._sync_count(), 0)

    # ------------------------------------------------------------------
    # T6 — NO_ACTION → no write (ignore gate)
    # ------------------------------------------------------------------
    def test_no_action_writes_nothing(self):
        result = self._call(event_type="NO_ACTION")

        self.assertTrue(result["ok"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["reason"], "NO_ACTION")
        self.assertEqual(self._sync_count(), 0)

    # ------------------------------------------------------------------
    # T7 — LOW priority queueable event → no write
    # ------------------------------------------------------------------
    def test_low_priority_queueable_event_writes_nothing(self):
        # HOT_LEAD_BOOKING is normally queueable but LOW priority blocks it.
        result = self._call(event_type="HOT_LEAD_BOOKING", priority="LOW")

        self.assertTrue(result["ok"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["reason"], "LOW_PRIORITY")
        self.assertEqual(self._sync_count(), 0)

    # ------------------------------------------------------------------
    # T8 — recommended_channel None → no write (NO_CHANNEL gate)
    # ------------------------------------------------------------------
    def test_recommended_channel_none_writes_nothing(self):
        result = self._call(recommended_channel=None)

        self.assertTrue(result["ok"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["reason"], "NO_CHANNEL")
        self.assertEqual(self._sync_count(), 0)

    # ------------------------------------------------------------------
    # T8b — recommended_channel "NONE" → no write (NO_CHANNEL gate)
    # ------------------------------------------------------------------
    def test_recommended_channel_string_none_writes_nothing(self):
        result = self._call(recommended_channel="NONE")

        self.assertTrue(result["ok"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["reason"], "NO_CHANNEL")
        self.assertEqual(self._sync_count(), 0)

    # ------------------------------------------------------------------
    # T9 — unknown event_type → raises ValueError
    # ------------------------------------------------------------------
    def test_unknown_event_type_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self._call(event_type="TOTALLY_UNKNOWN_TYPE")

        self.assertIn("TOTALLY_UNKNOWN_TYPE", str(ctx.exception))

    # ------------------------------------------------------------------
    # T10 — duplicate call → no second NEEDS_SYNC row inserted
    # ------------------------------------------------------------------
    def test_duplicate_call_does_not_create_second_pending_record(self):
        result1 = self._call(event_type="NUDGE_PROGRESS", priority="HIGH")
        result2 = self._call(event_type="NUDGE_PROGRESS", priority="HIGH")

        self.assertTrue(result1["wrote"])
        self.assertTrue(result2["wrote"])  # update path still returns wrote=True
        self.assertEqual(
            self._sync_count(), 1,
            "A second call for the same event_type must not insert a duplicate row",
        )

    # ------------------------------------------------------------------
    # T11 — Row content correct on first write
    # ------------------------------------------------------------------
    def test_row_content_correct_on_first_write(self):
        self._call(event_type="HOT_LEAD_BOOKING", priority="HIGH")

        rows = self._sync_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]

        self.assertEqual(row["lead_id"], LEAD_ID)
        self.assertEqual(row["destination"], "CORY_BOOKING")
        self.assertEqual(row["status"], "NEEDS_SYNC")
        self.assertEqual(row["reason"], "HOT_LEAD_BOOKING")
        self.assertEqual(row["created_at"], BUILT_AT)
        self.assertEqual(row["updated_at"], BUILT_AT)

    # ------------------------------------------------------------------
    # T12 — Missing lead_id raises ValueError
    # ------------------------------------------------------------------
    def test_blank_lead_id_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self._call(lead_id="")

        self.assertIn("lead_id", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # T13 — Lead not found in DB → ok=False, reason=LEAD_NOT_FOUND
    # ------------------------------------------------------------------
    def test_lead_not_found_in_db_returns_ok_false(self):
        result = consume_cory_recommendation(
            _payload(lead_id="NO_SUCH_LEAD_XYZ"),
            db_path=TEST_DB_PATH,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")
        # No rows written for the nonexistent lead.
        self.assertEqual(len(self._sync_rows("NO_SUCH_LEAD_XYZ")), 0)


if __name__ == "__main__":
    unittest.main()
