"""
tests/test_run_stale_progress_scan.py

Unit tests for services/worker/run_stale_progress_scan.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.run_stale_progress_scan import run_stale_progress_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_stale_progress_scan.db")

_TS_CREATED  = "2026-01-01T00:00:00Z"
_TS_STARTED  = "2026-02-01T00:00:00Z"
_TS_ACTIVITY = "2026-02-10T00:00:00Z"


def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000", _TS_CREATED, _TS_CREATED),
    )
    conn.commit()
    conn.close()


def _seed_course_state(
    lead_id: str,
    completion_pct: float | None = 40.0,
    started_at: str | None = _TS_STARTED,
    last_activity_at: str | None = _TS_ACTIVITY,
) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state"
        " (lead_id, course_id, started_at, completion_pct, last_activity_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "FREE_INTRO_AI_V0", started_at, completion_pct, last_activity_at, _TS_CREATED),
    )
    conn.commit()
    conn.close()


class TestRunStaleProgressScan(unittest.TestCase):

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

    # ------------------------------------------------------------------
    # T1 — empty DB → count 0, lead_ids []
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_zero_count(self):
        """T1: no leads → count=0, lead_ids=[]."""
        result = run_stale_progress_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["scan_name"], "STALE_PROGRESS_SCAN")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])
        self.assertEqual(result["limit_used"], 100)
        tc = result["threshold_counts"]
        self.assertIn("threshold_counts", result)
        self.assertEqual(tc["INACTIVE_48H"], 0)
        self.assertEqual(tc["INACTIVE_4D"],  0)
        self.assertEqual(tc["INACTIVE_7D"],  0)
        self.assertEqual(tc["NONE"],         0)

    # ------------------------------------------------------------------
    # T2 — one qualifying stale-progress lead → count 1 and correct lead_id
    # ------------------------------------------------------------------
    def test_t2_one_stale_progress_lead(self):
        """T2: started + incomplete + last_activity → count=1, correct lead_id."""
        _seed_lead("lead-a")
        _seed_course_state("lead-a", completion_pct=40.0)
        result = run_stale_progress_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn("lead-a", result["lead_ids"])
        self.assertIn("threshold_counts", result)
        self.assertEqual(sum(result["threshold_counts"].values()), result["count"])

    # ------------------------------------------------------------------
    # T3 — completed lead excluded
    # ------------------------------------------------------------------
    def test_t3_completed_lead_excluded(self):
        """T3: completion_pct=100 → excluded from results."""
        _seed_lead("lead-b")
        _seed_course_state("lead-b", completion_pct=100.0)
        result = run_stale_progress_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])

    # ------------------------------------------------------------------
    # T4 — limit respected through the worker wrapper
    # ------------------------------------------------------------------
    def test_t4_limit_respected(self):
        """T4: three qualifying leads, limit=2 → count=2."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_course_state(i, completion_pct=50.0)
        result = run_stale_progress_scan(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["lead_ids"]), 2)
        self.assertEqual(result["limit_used"], 2)
        self.assertEqual(sum(result["threshold_counts"].values()), result["count"])


if __name__ == "__main__":
    unittest.main()
