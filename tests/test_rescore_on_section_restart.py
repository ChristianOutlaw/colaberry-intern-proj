"""
tests/test_rescore_on_section_restart.py

Unit tests for execution/progress/rescore_on_section_restart.py.
No SQLite, no filesystem, no network — pure function tests only.
All tests inject a fixed _NOW datetime; datetime.now() is never called.

Restart detection rule: current_completion_pct < previous_completion_pct.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.progress.rescore_on_section_restart import (  # noqa: E402
    rescore_on_section_restart,
)

_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)

# Shared base kwargs for the scoring signals
_BASE = dict(
    now=_NOW,
    invite_sent=True,
    last_activity_at=(_NOW - timedelta(days=3)).isoformat(),
    started_at=(_NOW - timedelta(days=20)).isoformat(),
    avg_quiz_score=70.0,
    avg_quiz_attempts=1.5,
    reflection_confidence=None,
    current_section="section-3",
)


class TestRescoreOnSectionRestart(unittest.TestCase):

    # ------------------------------------------------------------------
    # Scenario A — forward progress → no rescore (returns None)
    # ------------------------------------------------------------------

    def test_scenario_a_forward_progress_returns_none(self):
        """Scenario A: completion went up (30 → 50) → no restart, returns None."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=30.0,
            current_completion_pct=50.0,
        )
        self.assertIsNone(result)

    def test_scenario_a_same_completion_returns_none(self):
        """Scenario A: completion unchanged (40 → 40) → not a restart, returns None."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=40.0,
            current_completion_pct=40.0,
        )
        self.assertIsNone(result)

    def test_scenario_a_no_previous_completion_returns_none(self):
        """Scenario A: previous is None (first time seen) → unknown state, returns None."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=None,
            current_completion_pct=50.0,
        )
        self.assertIsNone(result)

    def test_scenario_a_no_current_completion_returns_none(self):
        """Scenario A: current is None (no events) → cannot detect restart, returns None."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=30.0,
            current_completion_pct=None,
        )
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Scenario B — completion dropped → restart detected, rescore returned
    # ------------------------------------------------------------------

    def test_scenario_b_completion_drop_triggers_rescore(self):
        """Scenario B: completion dropped (50 → 30) → restart detected, score returned."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=50.0,
            current_completion_pct=30.0,
        )
        self.assertIsNotNone(result)

    def test_scenario_b_result_has_expected_shape(self):
        """Scenario B: returned score dict has all required keys."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=60.0,
            current_completion_pct=20.0,
        )
        for key in ("score", "signal", "reason_codes", "reason_summary", "evaluated_at"):
            self.assertIn(key, result, f"Missing key in rescore result: {key}")

    def test_scenario_b_score_reflects_lower_completion(self):
        """Scenario B: post-restart score is lower than a pre-restart score."""
        score_before = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=0.0,   # simulate: was at 0, now at 80 → forward, returns None
            current_completion_pct=80.0,
        )
        # forward → None; compute the 80% score directly for comparison
        from execution.leads.compute_lead_temperature import compute_lead_temperature
        high_score = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=80.0,
            last_activity_at=_BASE["last_activity_at"],
            started_at=_BASE["started_at"],
            avg_quiz_score=70.0,
            avg_quiz_attempts=1.5,
            reflection_confidence=None,
            current_section="section-3",
        )

        # Restart: was at 80%, dropped back to 20%
        result_after_restart = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=80.0,
            current_completion_pct=20.0,
        )
        self.assertIsNone(score_before)  # Scenario A: 0 → 80 is forward progress
        self.assertIsNotNone(result_after_restart)
        self.assertLess(
            result_after_restart["score"],
            high_score["score"],
            "Post-restart score should be lower than the pre-restart score.",
        )

    def test_scenario_b_minimal_drop_also_triggers(self):
        """Scenario B: even a 1-point drop is a restart signal."""
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=50.0,
            current_completion_pct=49.0,
        )
        self.assertIsNotNone(result)

    def test_scenario_b_score_uses_current_completion(self):
        """Scenario B: rescored completion_percent matches the current (post-restart) value."""
        from execution.leads.compute_lead_temperature import compute_lead_temperature
        expected = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=10.0,
            last_activity_at=_BASE["last_activity_at"],
            started_at=_BASE["started_at"],
            avg_quiz_score=70.0,
            avg_quiz_attempts=1.5,
            reflection_confidence=None,
            current_section="section-3",
        )
        result = rescore_on_section_restart(
            **_BASE,
            previous_completion_pct=70.0,
            current_completion_pct=10.0,
        )
        self.assertEqual(result["score"], expected["score"])
        self.assertEqual(result["signal"], expected["signal"])


if __name__ == "__main__":
    unittest.main()
