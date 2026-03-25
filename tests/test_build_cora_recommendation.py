"""
tests/test_build_cora_recommendation.py

Unit tests for execution/decision/build_cora_recommendation.py.
Covers the test matrix in directives/CORA_RECOMMENDATION_EVENTS.md.

No SQLite, no filesystem, no network — pure function tests only.
All tests inject a fixed `_NOW` datetime; datetime.now() is never called.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.decision.build_cora_recommendation import (  # noqa: E402
    CHANNEL_CALL,
    CHANNEL_EMAIL,
    EVENT_HOT_BOOKING,
    EVENT_NO_ACTION,
    EVENT_NUDGE_PROGRESS,
    EVENT_REENGAGE,
    EVENT_SEND_INVITE,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STALL_DAYS,
    build_cora_recommendation,
)

# ---------------------------------------------------------------------------
# Fixed injected clock (2026-02-25 12:00:00 UTC) — shared across all tests.
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    """Return an ISO-8601 UTC string for exactly `days_ago` days before _NOW."""
    return (_NOW - timedelta(days=days_ago)).isoformat()


# Minimal valid kwargs shared across multiple tests (all optional signals absent).
_BASE = dict(
    now=_NOW,
    lead_id="lead-test-001",
    current_section=None,
    temperature_signal=None,
    temperature_score=None,
    reason_codes=[],
)


class TestBuildCoraRecommendation(unittest.TestCase):

    # ------------------------------------------------------------------
    # T1 — Not invited → SEND_INVITE
    # ------------------------------------------------------------------
    def test_t1_not_invited_sends_invite(self):
        """T1: invite_sent=False → SEND_INVITE, LOW priority, EMAIL channel."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=False,
            completion_percent=None,
            last_activity_at=None,
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"],          EVENT_SEND_INVITE)
        self.assertEqual(result["priority"],            PRIORITY_LOW)
        self.assertEqual(result["recommended_channel"], CHANNEL_EMAIL)
        self.assertIn("NOT_INVITED", result["reason_codes"])

    # ------------------------------------------------------------------
    # T2 — Invited, completion None → NUDGE_PROGRESS (INVITED_NO_START)
    # ------------------------------------------------------------------
    def test_t2_invited_not_started_completion_none(self):
        """T2: invited, completion_percent=None → NUDGE_PROGRESS with INVITED_NO_START."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=None,
            last_activity_at=None,
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"],          EVENT_NUDGE_PROGRESS)
        self.assertEqual(result["priority"],            PRIORITY_MEDIUM)
        self.assertEqual(result["recommended_channel"], CHANNEL_EMAIL)
        self.assertIn("INVITED_NO_START", result["reason_codes"])

    # ------------------------------------------------------------------
    # T3 — Invited, completion 0.0 → NUDGE_PROGRESS (INVITED_NO_START)
    # ------------------------------------------------------------------
    def test_t3_invited_not_started_completion_zero(self):
        """T3: invited, completion_percent=0.0 → NUDGE_PROGRESS with INVITED_NO_START."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=0.0,
            last_activity_at=None,
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"], EVENT_NUDGE_PROGRESS)
        self.assertIn("INVITED_NO_START", result["reason_codes"])

    # ------------------------------------------------------------------
    # T4 — HOT signal → HOT_LEAD_BOOKING
    # ------------------------------------------------------------------
    def test_t4_hot_lead_gets_booking_event(self):
        """T4: hot_signal=HOT + 100% completion → HOT_LEAD_BOOKING, HIGH priority, CALL channel."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(3),
            hot_signal="HOT",
        )
        self.assertEqual(result["event_type"],          EVENT_HOT_BOOKING)
        self.assertEqual(result["priority"],            PRIORITY_HIGH)
        self.assertEqual(result["recommended_channel"], CHANNEL_CALL)
        self.assertIn("HOT_SIGNAL_ACTIVE", result["reason_codes"])

    # ------------------------------------------------------------------
    # T5 — HOT beats stale: HOT_LEAD_BOOKING even with stale activity
    # ------------------------------------------------------------------
    def test_t5_hot_signal_beats_stale_activity(self):
        """T5: hot_signal=HOT + 100% completion with stale activity → HOT_LEAD_BOOKING, not REENGAGE."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(25),
            hot_signal="HOT",
        )
        self.assertEqual(result["event_type"], EVENT_HOT_BOOKING)

    # ------------------------------------------------------------------
    # T19 — HOT signal without 100% completion must NOT trigger booking
    # ------------------------------------------------------------------
    def test_t19_hot_signal_partial_completion_no_booking(self):
        """T19: hot_signal=HOT but completion=25% → must NOT return HOT_LEAD_BOOKING."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=25.0,
            last_activity_at=_iso(3),
            hot_signal="HOT",
        )
        self.assertNotEqual(result["event_type"], EVENT_HOT_BOOKING)

    # ------------------------------------------------------------------
    # T6 — Stalled lead (> STALL_DAYS inactive) → REENGAGE_STALLED_LEAD
    # ------------------------------------------------------------------
    def test_t6_stalled_lead_reengage(self):
        """T6: started, activity > STALL_DAYS → REENGAGE_STALLED_LEAD, HIGH, CALL."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=33.0,
            last_activity_at=_iso(STALL_DAYS + 1),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"],          EVENT_REENGAGE)
        self.assertEqual(result["priority"],            PRIORITY_HIGH)
        self.assertEqual(result["recommended_channel"], CHANNEL_CALL)
        self.assertIn("ACTIVITY_STALLED", result["reason_codes"])

    # ------------------------------------------------------------------
    # T7 — Started with no activity recorded → REENGAGE_STALLED_LEAD
    # ------------------------------------------------------------------
    def test_t7_started_no_activity_reengage(self):
        """T7: completion > 0 but last_activity_at=None → REENGAGE_STALLED_LEAD."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=15.0,
            last_activity_at=None,
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"], EVENT_REENGAGE)
        self.assertIn("ACTIVITY_STALLED", result["reason_codes"])

    # ------------------------------------------------------------------
    # T8 — In-progress, recently active, not hot → NUDGE_PROGRESS
    # ------------------------------------------------------------------
    def test_t8_active_warm_lead_nudge_progress(self):
        """T8: started, activity within STALL_DAYS, not hot → NUDGE_PROGRESS, MEDIUM, EMAIL."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=33.0,
            last_activity_at=_iso(5),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"],          EVENT_NUDGE_PROGRESS)
        self.assertEqual(result["priority"],            PRIORITY_MEDIUM)
        self.assertEqual(result["recommended_channel"], CHANNEL_EMAIL)
        self.assertIn("ACTIVE_LEARNER", result["reason_codes"])

    # ------------------------------------------------------------------
    # T9 — Exactly at boundary: STALL_DAYS days inactive → NUDGE_PROGRESS (not REENGAGE)
    # ------------------------------------------------------------------
    def test_t9_at_stall_boundary_nudge_not_reengage(self):
        """T9: exactly STALL_DAYS inactive → NUDGE_PROGRESS (boundary is exclusive)."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=33.0,
            last_activity_at=_iso(STALL_DAYS),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"], EVENT_NUDGE_PROGRESS)

    # ------------------------------------------------------------------
    # T10 — Completed course, not hot → NO_ACTION
    # ------------------------------------------------------------------
    def test_t10_completed_course_warm_review(self):
        """T10: completion=100, not hot → WARM_REVIEW, LOW priority, no channel."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(2),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"],          "WARM_REVIEW")
        self.assertEqual(result["priority"],            PRIORITY_LOW)
        self.assertIsNone(result["recommended_channel"])
        self.assertIn("COURSE_COMPLETE", result["reason_codes"])

    # ------------------------------------------------------------------
    # T20 — Completed + not hot → WARM_REVIEW (never NO_ACTION or COURSE_COMPLETED)
    # ------------------------------------------------------------------
    def test_t20_completed_not_hot_is_warm_review(self):
        """T20: completion=100, hot_signal=NOT_HOT → WARM_REVIEW, not NO_ACTION."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(1),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["event_type"], "WARM_REVIEW")
        self.assertNotEqual(result["event_type"], EVENT_NO_ACTION)

    # ------------------------------------------------------------------
    # T11 — Output shape is always complete
    # ------------------------------------------------------------------
    def test_t11_output_shape_is_complete(self):
        """T11: every call returns all required keys with correct types."""
        result = build_cora_recommendation(
            **{
                **_BASE,
                "temperature_signal": "WARM",
                "temperature_score": 48,
                "reason_codes": ["COMPLETION_MODERATE", "ACTIVITY_MODERATE"],
            },
            invite_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(4),
            hot_signal="NOT_HOT",
        )
        for key in (
            "lead_id", "event_type", "priority", "reason_codes",
            "recommended_channel", "payload", "status", "built_at",
        ):
            self.assertIn(key, result, f"Missing key: {key}")

        self.assertIsInstance(result["lead_id"],      str)
        self.assertIsInstance(result["event_type"],   str)
        self.assertIsInstance(result["priority"],     str)
        self.assertIsInstance(result["reason_codes"], list)
        self.assertIsInstance(result["payload"],      dict)
        self.assertEqual(result["status"],            "READY")
        self.assertTrue(result["built_at"].endswith("Z"))
        self.assertIn("2026-02-25", result["built_at"])

        for key in (
            "completion_percent", "current_section", "days_inactive",
            "hot_signal", "temperature_signal", "temperature_score",
            "upstream_reason_codes",
        ):
            self.assertIn(key, result["payload"], f"Missing payload key: {key}")

    # ------------------------------------------------------------------
    # T12 — Upstream reason_codes are passed through in payload
    # ------------------------------------------------------------------
    def test_t12_upstream_reason_codes_in_payload(self):
        """T12: input reason_codes appear in payload.upstream_reason_codes."""
        codes = ["COMPLETION_LOW", "ACTIVITY_DORMANT"]
        result = build_cora_recommendation(
            **{**_BASE, "reason_codes": codes},
            invite_sent=True,
            completion_percent=8.0,
            last_activity_at=_iso(20),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["payload"]["upstream_reason_codes"], codes)

    # ------------------------------------------------------------------
    # T13 — lead_id is echoed through unchanged
    # ------------------------------------------------------------------
    def test_t13_lead_id_echoed(self):
        """T13: lead_id in output matches input."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(3),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["lead_id"], _BASE["lead_id"])

    # ------------------------------------------------------------------
    # T14 — Constants match directive values
    # ------------------------------------------------------------------
    def test_t14_constants_match_directive(self):
        """T14: locked constants must match directives/CORA_RECOMMENDATION_EVENTS.md."""
        self.assertEqual(STALL_DAYS,    14)
        self.assertEqual(PRIORITY_HIGH,   "HIGH")
        self.assertEqual(PRIORITY_MEDIUM, "MEDIUM")
        self.assertEqual(PRIORITY_LOW,    "LOW")
        self.assertEqual(CHANNEL_EMAIL,   "EMAIL")
        self.assertEqual(CHANNEL_CALL,    "CALL")

    # ------------------------------------------------------------------
    # T15 — Raises ValueError for missing now
    # ------------------------------------------------------------------
    def test_t15_raises_on_none_now(self):
        """T15: passing now=None raises ValueError."""
        with self.assertRaises(ValueError):
            build_cora_recommendation(
                now=None,
                lead_id="lead-001",
                invite_sent=True,
                completion_percent=50.0,
                current_section=None,
                last_activity_at=None,
                hot_signal="NOT_HOT",
                temperature_signal=None,
                temperature_score=None,
                reason_codes=[],
            )

    # ------------------------------------------------------------------
    # T16 — Raises ValueError for empty lead_id
    # ------------------------------------------------------------------
    def test_t16_raises_on_empty_lead_id(self):
        """T16: passing lead_id='' raises ValueError."""
        with self.assertRaises(ValueError):
            build_cora_recommendation(
                now=_NOW,
                lead_id="",
                invite_sent=True,
                completion_percent=50.0,
                current_section=None,
                last_activity_at=None,
                hot_signal="NOT_HOT",
                temperature_signal=None,
                temperature_score=None,
                reason_codes=[],
            )

    # ------------------------------------------------------------------
    # T17 — days_inactive in payload is correct
    # ------------------------------------------------------------------
    def test_t17_days_inactive_computed_in_payload(self):
        """T17: payload.days_inactive reflects elapsed days from last_activity_at to now."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=True,
            completion_percent=40.0,
            last_activity_at=_iso(7),
            hot_signal="NOT_HOT",
        )
        self.assertEqual(result["payload"]["days_inactive"], 7)

    # ------------------------------------------------------------------
    # T18 — days_inactive is None when last_activity_at is None
    # ------------------------------------------------------------------
    def test_t18_days_inactive_none_when_no_activity(self):
        """T18: payload.days_inactive is None when last_activity_at is None."""
        result = build_cora_recommendation(
            **_BASE,
            invite_sent=False,
            completion_percent=None,
            last_activity_at=None,
            hot_signal="NOT_HOT",
        )
        self.assertIsNone(result["payload"]["days_inactive"])


if __name__ == "__main__":
    unittest.main()
