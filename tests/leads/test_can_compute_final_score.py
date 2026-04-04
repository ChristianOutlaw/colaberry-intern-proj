"""
tests/test_can_compute_final_score.py

Unit tests for execution/leads/can_compute_final_score.py.
Pure function — no DB, no I/O.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from execution.leads.can_compute_final_score import can_compute_final_score

_FULL_ROW = {
    "invite_sent":        True,
    "has_quiz_data":      True,
    "has_reflection_data": True,
}


class TestCanComputeFinalScore(unittest.TestCase):

    def test_all_inputs_present_returns_true(self):
        """All three required inputs True -> True."""
        self.assertTrue(can_compute_final_score(_FULL_ROW))

    def test_invite_sent_false_returns_false(self):
        """invite_sent=False -> False."""
        self.assertFalse(can_compute_final_score({**_FULL_ROW, "invite_sent": False}))

    def test_has_quiz_data_none_returns_false(self):
        """has_quiz_data=None -> False."""
        self.assertFalse(can_compute_final_score({**_FULL_ROW, "has_quiz_data": None}))

    def test_has_quiz_data_false_returns_false(self):
        """has_quiz_data=False -> False."""
        self.assertFalse(can_compute_final_score({**_FULL_ROW, "has_quiz_data": False}))

    def test_has_reflection_data_false_returns_false(self):
        """has_reflection_data=False -> False."""
        self.assertFalse(can_compute_final_score({**_FULL_ROW, "has_reflection_data": False}))

    def test_missing_keys_returns_false(self):
        """Empty dict (all keys missing) -> False."""
        self.assertFalse(can_compute_final_score({}))


if __name__ == "__main__":
    unittest.main()
