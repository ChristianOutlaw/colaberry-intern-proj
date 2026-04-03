"""
tests/test_derive_lead_lifecycle_state.py

Unit tests for execution/leads/derive_lead_lifecycle_state.py.

No SQLite, no filesystem, no network — pure function tests only.
All tests inject a fixed _NOW datetime; datetime.now() is never called.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.derive_lead_lifecycle_state import (  # noqa: E402
    STATE_BOOKING_READY,
    STATE_COMPLETED_REENGAGE,
    STATE_COMPLETED_WARM,
    STATE_INVITED_NOT_STARTED,
    STATE_NOT_INVITED,
    STATE_STARTED_ACTIVE,
    STATE_STARTED_STALE,
    STALL_DAYS,
    derive_lead_lifecycle_state,
)

# ---------------------------------------------------------------------------
# Fixed clock shared across all tests (same epoch as other test modules)
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    """Return an ISO-8601 UTC string for exactly `days_ago` days before _NOW."""
    return (_NOW - timedelta(days=days_ago)).isoformat()


class TestDeriveLeadLifecycleState(unittest.TestCase):

    # ------------------------------------------------------------------
    # Scenario A — uninvited lead maps to NOT_INVITED
    # ------------------------------------------------------------------
    def test_scenario_a_not_invited(self):
        """Scenario A: no invite → NOT_INVITED regardless of other signals."""
        state = derive_lead_lifecycle_state(
            invite_sent=False,
            completion_percent=None,
            last_activity_at=None,
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_NOT_INVITED)

    def test_scenario_a_not_invited_even_with_hot_signal(self):
        """Not invited + HOT signal still returns NOT_INVITED (invite check is first)."""
        state = derive_lead_lifecycle_state(
            invite_sent=False,
            completion_percent=100.0,
            last_activity_at=_iso(1),
            hot_signal="HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_NOT_INVITED)

    # ------------------------------------------------------------------
    # Scenario B — invited, not started → INVITED_NOT_STARTED
    # ------------------------------------------------------------------
    def test_scenario_b_invited_completion_none(self):
        """Scenario B: invite sent, completion=None → INVITED_NOT_STARTED."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=None,
            last_activity_at=None,
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_INVITED_NOT_STARTED)

    def test_scenario_b_invited_completion_zero(self):
        """Scenario B: invite sent, completion=0.0 → INVITED_NOT_STARTED."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=0.0,
            last_activity_at=None,
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_INVITED_NOT_STARTED)

    # ------------------------------------------------------------------
    # Scenario C — in-progress: active vs stale
    # ------------------------------------------------------------------
    def test_scenario_c_started_active(self):
        """Scenario C (active): in progress, activity within STALL_DAYS → STARTED_ACTIVE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(3),
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_STARTED_ACTIVE)

    def test_scenario_c_started_stale_exceeds_threshold(self):
        """Scenario C (stale): in progress, inactive > STALL_DAYS → STARTED_STALE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(STALL_DAYS + 1),
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_STARTED_STALE)

    def test_scenario_c_started_stale_no_activity(self):
        """Scenario C (stale): in progress, last_activity_at=None → STARTED_STALE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=25.0,
            last_activity_at=None,
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_STARTED_STALE)

    def test_scenario_c_boundary_exactly_stall_days_is_active(self):
        """Exactly STALL_DAYS inactive is not > STALL_DAYS, so STARTED_ACTIVE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(STALL_DAYS),
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_STARTED_ACTIVE)

    # ------------------------------------------------------------------
    # Scenario D — completed lead: BOOKING_READY / COMPLETED_WARM / COMPLETED_REENGAGE
    # ------------------------------------------------------------------
    def test_scenario_d_booking_ready_hot_and_complete(self):
        """Scenario D (hot): completion=100 + HOT → BOOKING_READY."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(2),
            hot_signal="HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_BOOKING_READY)

    def test_scenario_d_completed_warm_not_stale(self):
        """Scenario D (warm): completion=100, not hot, activity recent → COMPLETED_WARM."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(2),
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_COMPLETED_WARM)

    def test_scenario_d_completed_reengage_stale(self):
        """Scenario D (reengage): completion=100, not hot, inactive > STALL_DAYS → COMPLETED_REENGAGE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(STALL_DAYS + 1),
            hot_signal="NOT_HOT",
            now=_NOW,
        )
        self.assertEqual(state, STATE_COMPLETED_REENGAGE)

    def test_scenario_d_hot_partial_not_booking_ready(self):
        """Partial completion + HOT does NOT reach BOOKING_READY; maps to STARTED_ACTIVE."""
        state = derive_lead_lifecycle_state(
            invite_sent=True,
            completion_percent=80.0,
            last_activity_at=_iso(1),
            hot_signal="HOT",
            now=_NOW,
        )
        self.assertNotEqual(state, STATE_BOOKING_READY)
        self.assertEqual(state, STATE_STARTED_ACTIVE)


if __name__ == "__main__":
    unittest.main()
