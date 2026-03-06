"""
tests/test_compute_lead_temperature.py

Unit tests for execution/leads/compute_lead_temperature.py.
Covers test matrix T1–T10 from directives/LEAD_TEMPERATURE_SCORING.md.

No SQLite, no filesystem, no network — pure function tests only.
All tests inject a fixed `_NOW` datetime; datetime.now() is never called.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.leads.compute_lead_temperature import (  # noqa: E402
    INVITE_CAP,
    MAX_RETRY_PENALTY,
    SCORE_HOT,
    SCORE_WARM,
    W_COMPLETION,
    W_QUIZ,
    W_RECENCY,
    W_REFLECTION,
    compute_lead_temperature,
)

# ---------------------------------------------------------------------------
# Fixed injected clock used by every test (2026-02-25 12:00:00 UTC).
# Chosen to match the examples documented in LEAD_TEMPERATURE_SCORING.md.
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    """Return an ISO-8601 UTC string for exactly `days_ago` days before _NOW."""
    ts = _NOW - timedelta(days=days_ago)
    return ts.isoformat().replace("+00:00", "+00:00")  # keep explicit offset


class TestComputeLeadTemperature(unittest.TestCase):

    # ------------------------------------------------------------------
    # T1 — Highly engaged lead → HOT
    # ------------------------------------------------------------------
    def test_t1_highly_engaged_is_hot(self):
        """T1: completion=90, 2 days active, quiz=88, LOW retries, HIGH reflection → HOT."""
        # Expected score:
        #   completion: int(90 * 40/100) = 36
        #   recency:    2 days ≤ 7 → 25
        #   quiz:       int(88 * 20/100) = 17
        #   reflection: HIGH → 15
        #   retry:      1.0 ≤ 1.5 → 0
        #   raw = 93 → HOT (≥ 70)
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=90.0,
            last_activity_at=_iso(2),
            avg_quiz_score=88.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="HIGH",
            current_section="section-7",
        )
        self.assertEqual(result["signal"], "HOT")
        self.assertGreaterEqual(result["score"], SCORE_HOT)
        self.assertIn("COMPLETION_STRONG", result["reason_codes"])
        self.assertIn("RECENTLY_ACTIVE", result["reason_codes"])
        self.assertIn("QUIZ_STRONG", result["reason_codes"])
        self.assertIn("REFLECTION_HIGH", result["reason_codes"])

    # ------------------------------------------------------------------
    # T2 — Mid-progress mixed performance → WARM
    # ------------------------------------------------------------------
    def test_t2_mid_progress_is_warm(self):
        """T2: completion=40, 12 days active, quiz=62, mild retries, MEDIUM reflection → WARM."""
        # Expected score:
        #   completion: int(40 * 40/100) = 16
        #   recency:    12 days ≤ 14 → 18
        #   quiz:       int(62 * 20/100) = 12
        #   reflection: MEDIUM → 9
        #   retry:      2.0 > 1.5, ≤ 2.5 → -5
        #   raw = 50 → WARM (35 ≤ 50 < 70)
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=40.0,
            last_activity_at=_iso(12),
            avg_quiz_score=62.0,
            avg_quiz_attempts=2.0,
            reflection_confidence="MEDIUM",
            current_section="section-3",
        )
        self.assertEqual(result["signal"], "WARM")
        self.assertGreaterEqual(result["score"], SCORE_WARM)
        self.assertLess(result["score"], SCORE_HOT)
        self.assertIn("COMPLETION_MODERATE", result["reason_codes"])
        self.assertIn("ACTIVITY_MODERATE", result["reason_codes"])
        self.assertIn("RETRY_MILD", result["reason_codes"])

    # ------------------------------------------------------------------
    # T3 — Inactive / low progress → COLD
    # ------------------------------------------------------------------
    def test_t3_inactive_low_progress_is_cold(self):
        """T3: completion=8, 45 days inactive, no quiz, LOW reflection → COLD."""
        # Expected score:
        #   completion: int(8 * 40/100) = 3
        #   recency:    45 days > 30 → 0  (ACTIVITY_DORMANT)
        #   quiz:       None → 10 (QUIZ_UNKNOWN)
        #   reflection: LOW → 3
        #   retry:      None → 0
        #   raw = 16 → COLD (< 35)
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=8.0,
            last_activity_at=_iso(45),
            avg_quiz_score=None,
            avg_quiz_attempts=None,
            reflection_confidence="LOW",
            current_section=None,
        )
        self.assertEqual(result["signal"], "COLD")
        self.assertLess(result["score"], SCORE_WARM)
        self.assertIn("COMPLETION_LOW", result["reason_codes"])
        self.assertIn("ACTIVITY_DORMANT", result["reason_codes"])
        self.assertIn("QUIZ_UNKNOWN", result["reason_codes"])
        self.assertIn("REFLECTION_LOW", result["reason_codes"])

    # ------------------------------------------------------------------
    # T4 — Not invited → COLD even with all other strong signals
    # ------------------------------------------------------------------
    def test_t4_not_invited_is_cold_and_capped(self):
        """T4: invited_sent=False caps score at INVITE_CAP=15 → COLD; NOT_INVITED in codes."""
        # Without invite gate, raw would be:
        #   24 (completion=60) + 25 (3 days) + 16 (quiz=80) + 15 (HIGH) = 80
        # With gate: min(80, 15) = 15 → COLD
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=False,
            completion_percent=60.0,
            last_activity_at=_iso(3),
            avg_quiz_score=80.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="HIGH",
            current_section="section-5",
        )
        self.assertEqual(result["signal"], "COLD")
        self.assertLessEqual(result["score"], INVITE_CAP)
        self.assertIn("NOT_INVITED", result["reason_codes"])

    # ------------------------------------------------------------------
    # T5 — All optional inputs are None → COLD (resilience to missing data)
    # ------------------------------------------------------------------
    def test_t5_all_none_is_cold(self):
        """T5: invited=True, all other signals None → COLD with neutral values applied."""
        # completion: None → 0
        # recency:    None → 0 (NO_ACTIVITY)
        # quiz:       None → 10 (QUIZ_UNKNOWN, half-credit)
        # reflection: None → 7 (REFLECTION_UNKNOWN, near-half)
        # retry:      None → 0
        # raw = 17 → COLD
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=None,
            last_activity_at=None,
            avg_quiz_score=None,
            avg_quiz_attempts=None,
            reflection_confidence=None,
            current_section=None,
        )
        self.assertEqual(result["signal"], "COLD")
        self.assertLess(result["score"], SCORE_WARM)
        self.assertIn("COMPLETION_NONE", result["reason_codes"])
        self.assertIn("NO_ACTIVITY", result["reason_codes"])
        self.assertIn("QUIZ_UNKNOWN", result["reason_codes"])
        self.assertIn("REFLECTION_UNKNOWN", result["reason_codes"])

    # ------------------------------------------------------------------
    # T6 — Stale activity (> 30 days) prevents HOT despite good completion
    # ------------------------------------------------------------------
    def test_t6_stale_activity_prevents_hot(self):
        """T6: completion=70, 40 days inactive → WARM not HOT; ACTIVITY_DORMANT in codes."""
        # completion: int(70 * 40/100) = 28
        # recency:    40 days > 30 → 0 (ACTIVITY_DORMANT)
        # quiz:       int(75 * 20/100) = 15
        # reflection: MEDIUM → 9
        # retry:      0
        # raw = 52 → WARM
        # Without stale: 28+25+15+9 = 77 → HOT — confirms dormancy matters
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=70.0,
            last_activity_at=_iso(40),
            avg_quiz_score=75.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="MEDIUM",
            current_section="section-6",
        )
        self.assertEqual(result["signal"], "WARM")
        self.assertIn("ACTIVITY_DORMANT", result["reason_codes"])

        # Confirm the same inputs with recent activity would be HOT
        result_fresh = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=70.0,
            last_activity_at=_iso(3),
            avg_quiz_score=75.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="MEDIUM",
            current_section="section-6",
        )
        self.assertEqual(result_fresh["signal"], "HOT")

    # ------------------------------------------------------------------
    # T7 — High retry friction prevents HOT
    # ------------------------------------------------------------------
    def test_t7_high_retry_friction_prevents_hot(self):
        """T7: avg_quiz_attempts=4.5 applies RETRY_HIGH (-15) and keeps score below HOT."""
        # completion: int(65 * 40/100) = 26
        # recency:    4 days ≤ 7 → 25
        # quiz:       int(70 * 20/100) = 14
        # reflection: MEDIUM → 9
        # retry:      4.5 > 3.5 → -15 (RETRY_HIGH)
        # raw = 26+25+14+9-15 = 59 → WARM
        # Without penalty: 74 → HOT
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=65.0,
            last_activity_at=_iso(4),
            avg_quiz_score=70.0,
            avg_quiz_attempts=4.5,
            reflection_confidence="MEDIUM",
            current_section="section-5",
        )
        self.assertEqual(result["signal"], "WARM")
        self.assertIn("RETRY_HIGH", result["reason_codes"])
        self.assertLess(result["score"], SCORE_HOT)

        # Confirm same inputs without retries would be HOT
        result_no_retry = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=65.0,
            last_activity_at=_iso(4),
            avg_quiz_score=70.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="MEDIUM",
            current_section="section-5",
        )
        self.assertEqual(result_no_retry["signal"], "HOT")

    # ------------------------------------------------------------------
    # T8 — HIGH vs LOW reflection tips the HOT/WARM boundary
    # ------------------------------------------------------------------
    def test_t8_reflection_high_tips_to_hot(self):
        """T8: HIGH reflection scores 15 vs LOW scores 3 — 12-pt swing changes classification."""
        # completion: int(65 * 40/100) = 26
        # recency:    4 days → 25
        # quiz:       int(70 * 20/100) = 14
        # HIGH: 26+25+14+15 = 80 → HOT
        # LOW:  26+25+14+3  = 68 → WARM
        shared = dict(
            now=_NOW,
            invited_sent=True,
            completion_percent=65.0,
            last_activity_at=_iso(4),
            avg_quiz_score=70.0,
            avg_quiz_attempts=1.0,
            current_section="section-5",
        )
        result_high = compute_lead_temperature(**shared, reflection_confidence="HIGH")
        result_low  = compute_lead_temperature(**shared, reflection_confidence="LOW")

        self.assertEqual(result_high["signal"], "HOT")
        self.assertIn("REFLECTION_HIGH", result_high["reason_codes"])

        self.assertEqual(result_low["signal"], "WARM")
        self.assertIn("REFLECTION_LOW", result_low["reason_codes"])

        # Confirm the point spread is exactly 12 (15 - 3)
        self.assertEqual(result_high["score"] - result_low["score"], 12)

    # ------------------------------------------------------------------
    # T9 — Output shape is always complete and valid
    # ------------------------------------------------------------------
    def test_t9_output_shape_is_complete(self):
        """T9: every call returns all five required keys with correct types."""
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=55.0,
            last_activity_at=_iso(5),
            avg_quiz_score=72.0,
            avg_quiz_attempts=1.8,
            reflection_confidence="MEDIUM",
            current_section="section-4",
        )
        # All keys present
        for key in ("signal", "score", "reason_codes", "reason_summary", "evaluated_at"):
            self.assertIn(key, result, f"Missing key: {key}")

        # signal is one of the three valid values
        self.assertIn(result["signal"], {"HOT", "WARM", "COLD"})

        # score is int in [0, 100]
        self.assertIsInstance(result["score"], int)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)

        # reason_codes is a non-empty list of strings
        self.assertIsInstance(result["reason_codes"], list)
        self.assertTrue(len(result["reason_codes"]) >= 4)
        for code in result["reason_codes"]:
            self.assertIsInstance(code, str)
            self.assertTrue(len(code) > 0)

        # reason_summary is a non-empty string
        self.assertIsInstance(result["reason_summary"], str)
        self.assertTrue(len(result["reason_summary"]) > 0)

        # evaluated_at is derived from injected now and ends with "Z"
        self.assertTrue(result["evaluated_at"].endswith("Z"), result["evaluated_at"])
        self.assertIn("2026-02-25", result["evaluated_at"])

    # ------------------------------------------------------------------
    # T10 — Module constants match locked directive values
    # ------------------------------------------------------------------
    def test_t10_constants_match_directive(self):
        """T10: locked constants must match directives/LEAD_TEMPERATURE_SCORING.md."""
        self.assertEqual(SCORE_HOT,          70)
        self.assertEqual(SCORE_WARM,         35)
        self.assertEqual(INVITE_CAP,         15)
        self.assertEqual(W_COMPLETION,       40)
        self.assertEqual(W_RECENCY,          25)
        self.assertEqual(W_QUIZ,             20)
        self.assertEqual(W_REFLECTION,       15)
        self.assertEqual(MAX_RETRY_PENALTY,  15)

    # ------------------------------------------------------------------
    # Extra: score is clamped to [0, 100] even with extreme inputs
    # ------------------------------------------------------------------
    def test_score_is_clamped_to_valid_range(self):
        """Extra: extreme inputs must not produce a score outside 0–100."""
        # All perfect inputs
        result_max = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=100.0,
            last_activity_at=_iso(0),
            avg_quiz_score=100.0,
            avg_quiz_attempts=1.0,
            reflection_confidence="HIGH",
            current_section="section-10",
        )
        self.assertLessEqual(result_max["score"], 100)
        self.assertGreaterEqual(result_max["score"], 0)

        # Maximum penalty scenario (not invited + max retry)
        result_min = compute_lead_temperature(
            now=_NOW,
            invited_sent=False,
            completion_percent=0.0,
            last_activity_at=_iso(365),
            avg_quiz_score=0.0,
            avg_quiz_attempts=10.0,
            reflection_confidence="LOW",
            current_section=None,
        )
        self.assertGreaterEqual(result_min["score"], 0)
        self.assertLessEqual(result_min["score"], 100)

    # ------------------------------------------------------------------
    # Extra: evaluated_at matches injected now exactly
    # ------------------------------------------------------------------
    def test_evaluated_at_matches_injected_now(self):
        """Extra: evaluated_at must equal injected now serialised as UTC with trailing 'Z'."""
        result = compute_lead_temperature(
            now=_NOW,
            invited_sent=True,
            completion_percent=50.0,
            last_activity_at=_iso(5),
            avg_quiz_score=None,
            avg_quiz_attempts=None,
            reflection_confidence=None,
            current_section=None,
        )
        self.assertEqual(result["evaluated_at"], "2026-02-25T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
