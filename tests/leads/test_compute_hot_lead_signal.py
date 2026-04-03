"""
tests/test_compute_hot_lead_signal.py

Unit tests for execution/leads/compute_hot_lead_signal.py.
Covers directive checklist T1–T12 from directives/HOT_LEAD_SIGNAL.md.

No SQLite, no filesystem, no network — pure function tests only.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.compute_hot_lead_signal import (  # noqa: E402
    ACTIVITY_WINDOW_DAYS,
    COMPLETION_THRESHOLD_PCT,
    compute_hot_lead_signal,
)

# Fixed injected clock used by every test (2026-02-24 12:00:00 UTC).
NOW = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)

# Module logger name used with assertLogs.
_LOGGER = "execution.leads.compute_hot_lead_signal"


class TestComputeHotLeadSignal(unittest.TestCase):

    # ------------------------------------------------------------------
    # T1 — All gates pass (30 %, 3 days ago, invited)
    # ------------------------------------------------------------------
    def test_t1_all_gates_pass(self):
        """T1: invited, 30 % complete, active 3 days ago → hot=True, HOT_ENGAGED."""
        last_activity = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=30.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertTrue(result["hot"])
        self.assertEqual(result["reasons"], ["HOT_ENGAGED"])

    # ------------------------------------------------------------------
    # T2 — Completion below threshold (10 %, invited, 2 days ago)
    # ------------------------------------------------------------------
    def test_t2_completion_below_threshold(self):
        """T2: completion 10 % < 25 % → hot=False, COMPLETION_BELOW_THRESHOLD."""
        last_activity = datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=10.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["COMPLETION_BELOW_THRESHOLD"])

    # ------------------------------------------------------------------
    # T3 — Activity stale (40 %, invited, 10 days ago)
    # ------------------------------------------------------------------
    def test_t3_activity_stale(self):
        """T3: completion 40 %, last activity 10 days ago → hot=False, ACTIVITY_STALE."""
        last_activity = datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=40.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["ACTIVITY_STALE"])

    # ------------------------------------------------------------------
    # T4 — Not invited (invite gate blocks all other fields)
    # ------------------------------------------------------------------
    def test_t4_not_invited_gate_blocks(self):
        """T4: invite_sent=False → hot=False, NOT_INVITED regardless of other fields."""
        last_activity = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=False,
            completion_percent=50.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["NOT_INVITED"])

    # ------------------------------------------------------------------
    # T5 — Completion unknown (invited, completion None, recent timestamp)
    # ------------------------------------------------------------------
    def test_t5_completion_unknown(self):
        """T5: completion_percent=None → hot=False, COMPLETION_UNKNOWN."""
        last_activity = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=None,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["COMPLETION_UNKNOWN"])

    # ------------------------------------------------------------------
    # T6 — No activity timestamp (invited, 35 %, last_activity_time=None)
    # ------------------------------------------------------------------
    def test_t6_no_activity_recorded(self):
        """T6: last_activity_time=None with valid completion → hot=False, NO_ACTIVITY_RECORDED."""
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=35.0,
            last_activity_time=None,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["NO_ACTIVITY_RECORDED"])

    # ------------------------------------------------------------------
    # T7 — Boundary: completion exactly 25.0 %, activity exactly 7 days ago
    # ------------------------------------------------------------------
    def test_t7_boundary_at_threshold(self):
        """T7: completion=25.0 (==threshold) and delta=7 days (==window) → hot=True, HOT_ENGAGED."""
        # now - 7 full days: timedelta(days=7).days == 7, which is <= ACTIVITY_WINDOW_DAYS
        last_activity = datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=25.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertTrue(result["hot"])
        self.assertEqual(result["reasons"], ["HOT_ENGAGED"])

    # ------------------------------------------------------------------
    # T8 — Boundary: completion 24.9 %, activity 6 days ago
    # ------------------------------------------------------------------
    def test_t8_boundary_just_below_completion(self):
        """T8: completion=24.9 < 25.0 → hot=False, COMPLETION_BELOW_THRESHOLD (activity not evaluated)."""
        last_activity = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=24.9,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["COMPLETION_BELOW_THRESHOLD"])

    # ------------------------------------------------------------------
    # T9 — Boundary: completion 25.0 %, activity exactly 8 days ago
    # ------------------------------------------------------------------
    def test_t9_boundary_just_outside_window(self):
        """T9: completion=25.0 passes gate 2; delta=8 days > window → hot=False, ACTIVITY_STALE."""
        # now - 8 full days: timedelta(days=8).days == 8, which is > ACTIVITY_WINDOW_DAYS
        last_activity = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=25.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["ACTIVITY_STALE"])

    # ------------------------------------------------------------------
    # T10 — Naive last_activity_time logs a warning; result stays deterministic
    # ------------------------------------------------------------------
    def test_t10_naive_last_activity_time_logs_warning(self):
        """T10: naive last_activity_time is treated as UTC; a WARNING is logged; result is deterministic."""
        # Naive datetime equivalent to 2026-02-21T12:00:00Z → 3 days ago → within window
        naive_last_activity = datetime(2026, 2, 21, 12, 0, 0)  # no tzinfo
        with self.assertLogs(_LOGGER, level="WARNING") as log_ctx:
            result = compute_hot_lead_signal(
                invite_sent=True,
                completion_percent=30.0,
                last_activity_time=naive_last_activity,
                now=NOW,
            )
        # Treated as UTC → 3 days ago → all gates pass
        self.assertTrue(result["hot"])
        self.assertEqual(result["reasons"], ["HOT_ENGAGED"])
        # Warning must mention the field name so callers can identify the source
        self.assertTrue(
            any("last_activity_time" in msg for msg in log_ctx.output),
            "Expected a warning mentioning 'last_activity_time'",
        )

    # ------------------------------------------------------------------
    # T11 — invite_sent=False overrides all other valid fields
    # ------------------------------------------------------------------
    def test_t11_not_invited_overrides_valid_fields(self):
        """T11: invite_sent=False with valid completion and recent activity → hot=False, NOT_INVITED."""
        last_activity = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=False,
            completion_percent=50.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertFalse(result["hot"])
        self.assertEqual(result["reasons"], ["NOT_INVITED"])

    # ------------------------------------------------------------------
    # T12 — evaluated_at matches injected now as ISO-8601 UTC with trailing "Z"
    # ------------------------------------------------------------------
    def test_t12_evaluated_at_matches_injected_now(self):
        """T12: evaluated_at must equal injected now serialised as UTC with trailing 'Z'."""
        last_activity = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_hot_lead_signal(
            invite_sent=True,
            completion_percent=30.0,
            last_activity_time=last_activity,
            now=NOW,
        )
        self.assertIn("evaluated_at", result)
        self.assertEqual(result["evaluated_at"], "2026-02-24T12:00:00Z")

    # ------------------------------------------------------------------
    # Constants sanity check — locks directive values against accidental edits
    # ------------------------------------------------------------------
    def test_constants_match_directive(self):
        """Module constants must match the values locked in directives/HOT_LEAD_SIGNAL.md."""
        self.assertEqual(COMPLETION_THRESHOLD_PCT, 25.0)
        self.assertEqual(ACTIVITY_WINDOW_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
