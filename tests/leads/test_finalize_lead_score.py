"""
tests/test_finalize_lead_score.py

Unit tests for execution/leads/finalize_lead_score.py.
Pure function — no DB, no fixtures.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT))

from execution.leads.finalize_lead_score import finalize_lead_score
from execution.leads.compute_lead_temperature import SCORE_HOT, SCORE_WARM


class TestFinalizeLeadScore(unittest.TestCase):

    # ------------------------------------------------------------------
    # Score-based path
    # ------------------------------------------------------------------
    def test_score_below_warm_returns_final_cold(self):
        """requires_finalization=True + score below warm -> FINAL_COLD."""
        payload = {"requires_finalization": True, "score": SCORE_WARM - 1}
        result = finalize_lead_score("lead-1", payload)
        self.assertEqual(result["final_label"], "FINAL_COLD")

    def test_score_at_warm_returns_final_warm(self):
        """requires_finalization=True + score == SCORE_WARM -> FINAL_WARM."""
        payload = {"requires_finalization": True, "score": SCORE_WARM}
        result = finalize_lead_score("lead-2", payload)
        self.assertEqual(result["final_label"], "FINAL_WARM")

    def test_score_at_hot_returns_final_hot(self):
        """requires_finalization=True + score == SCORE_HOT -> FINAL_HOT."""
        payload = {"requires_finalization": True, "score": SCORE_HOT}
        result = finalize_lead_score("lead-3", payload)
        self.assertEqual(result["final_label"], "FINAL_HOT")

    # ------------------------------------------------------------------
    # Fallback path (no score — hot_signal used)
    # ------------------------------------------------------------------
    def test_no_score_hot_signal_returns_final_hot(self):
        """requires_finalization=True + no score + hot_signal=HOT -> FINAL_HOT (fallback)."""
        payload = {"requires_finalization": True, "hot_signal": "HOT"}
        result = finalize_lead_score("lead-4", payload)
        self.assertEqual(result["final_label"], "FINAL_HOT")

    # ------------------------------------------------------------------
    # Non-finalization path
    # ------------------------------------------------------------------
    def test_requires_finalization_false_payload_unchanged(self):
        """requires_finalization=False -> payload returned unchanged."""
        payload = {"requires_finalization": False, "score": SCORE_HOT}
        result = finalize_lead_score("lead-5", payload)
        self.assertNotIn("final_label", result)


if __name__ == "__main__":
    unittest.main()
