"""
tests/test_find_stale_progress_leads.py

Unit tests for execution/scans/find_stale_progress_leads.py.
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
from execution.scans.find_stale_progress_leads import find_stale_progress_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_stale_progress_leads.db")

_TS_CREATED  = "2026-01-01T00:00:00Z"
_TS_STARTED  = "2026-02-01T00:00:00Z"
_TS_ACTIVITY = "2026-02-10T00:00:00Z"   # some time before now, classifiable


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
    started_at: str | None = _TS_STARTED,
    completion_pct: float | None = 40.0,
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


class TestFindStaleProgressLeads(unittest.TestCase):

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
    # T1 — empty DB returns empty list
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_empty_list(self):
        """T1: no leads in DB → empty result."""
        result = find_stale_progress_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — started, incomplete, has activity → returned with threshold key
    # ------------------------------------------------------------------
    def test_t2_stale_in_progress_lead_returned(self):
        """T2: started + incomplete + last_activity_at → included, stale_progress_threshold present."""
        _seed_lead("lead-a")
        _seed_course_state("lead-a", completion_pct=40.0)
        result = find_stale_progress_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-a")
        self.assertIn("stale_progress_threshold", result[0])
        threshold = result[0]["stale_progress_threshold"]
        self.assertTrue(
            threshold is None or threshold in ("INACTIVE_48H", "INACTIVE_4D", "INACTIVE_7D"),
            f"unexpected threshold value: {threshold!r}",
        )

    # ------------------------------------------------------------------
    # T3 — completed lead (completion_pct = 100) is excluded
    # ------------------------------------------------------------------
    def test_t3_completed_lead_excluded(self):
        """T3: completion_pct = 100 → excluded from results."""
        _seed_lead("lead-b")
        _seed_course_state("lead-b", completion_pct=100.0)
        result = find_stale_progress_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T4 — limit is respected
    # ------------------------------------------------------------------
    def test_t4_limit_is_respected(self):
        """T4: three qualifying leads, limit=2 → only 2 returned."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_course_state(i, completion_pct=50.0)
        result = find_stale_progress_leads(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
