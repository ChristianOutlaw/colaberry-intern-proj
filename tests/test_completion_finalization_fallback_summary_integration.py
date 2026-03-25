"""
tests/test_completion_finalization_fallback_summary_integration.py

Integration test: proves completion finalization scan → fallback label summary
is computed correctly end-to-end.

No mocks. Uses isolated SQLite DB.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.run_completion_finalization_scan import run_completion_finalization_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_completion_finalization_fallback_summary.db")

_TS_CREATED  = "2026-01-01T00:00:00Z"
_TS_STARTED  = "2026-02-01T00:00:00Z"
_TS_ACTIVITY = "2026-02-20T00:00:00Z"


def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000", _TS_CREATED, _TS_CREATED),
    )
    conn.commit()
    conn.close()


def _seed_course_state(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state"
        " (lead_id, course_id, started_at, completion_pct, last_activity_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "FREE_INTRO_AI_V0", _TS_STARTED, 100.0, _TS_ACTIVITY, _TS_CREATED),
    )
    conn.commit()
    conn.close()


class TestCompletionFinalizationFallbackSummaryIntegration(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_completed_lead_maps_to_final_warm_via_fallback(self):
        """
        Completed lead with no score and no hot_signal →
        fallback logic → FINAL_WARM.
        """
        _seed_lead("lead-a")
        _seed_course_state("lead-a")

        result = run_completion_finalization_scan(db_path=TEST_DB_PATH)

        self.assertEqual(result["count"], 1)

        summary = result["fallback_final_label_summary"]
        self.assertEqual(summary["FINAL_WARM"], 1)
        self.assertEqual(summary["FINAL_HOT"], 0)
        self.assertEqual(summary["FINAL_COLD"], 0)


if __name__ == "__main__":
    unittest.main()
