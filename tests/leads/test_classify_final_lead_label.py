"""
tests/test_classify_final_lead_label.py

Unit tests for execution/leads/classify_final_lead_label.py.
Pure function — no DB, no fixtures.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT))

from execution.leads.classify_final_lead_label import classify_final_lead_label
from execution.leads.compute_lead_temperature import SCORE_HOT, SCORE_WARM


class TestClassifyFinalLeadLabel(unittest.TestCase):

    def test_none_returns_final_cold(self):
        self.assertEqual(classify_final_lead_label(None), "FINAL_COLD")

    def test_below_warm_threshold_returns_final_cold(self):
        self.assertEqual(classify_final_lead_label(SCORE_WARM - 1), "FINAL_COLD")

    def test_exactly_warm_threshold_returns_final_warm(self):
        self.assertEqual(classify_final_lead_label(SCORE_WARM), "FINAL_WARM")

    def test_exactly_hot_threshold_returns_final_hot(self):
        self.assertEqual(classify_final_lead_label(SCORE_HOT), "FINAL_HOT")

    def test_between_warm_and_hot_returns_final_warm(self):
        mid = (SCORE_WARM + SCORE_HOT) // 2
        self.assertEqual(classify_final_lead_label(mid), "FINAL_WARM")

    def test_above_hot_threshold_returns_final_hot(self):
        self.assertEqual(classify_final_lead_label(SCORE_HOT + 10), "FINAL_HOT")


if __name__ == "__main__":
    unittest.main()
