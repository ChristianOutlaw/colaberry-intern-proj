"""
tests/test_run_no_start_scan.py

Unit tests for services/worker/run_no_start_scan.py.
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
from services.worker.run_no_start_scan import run_no_start_scan

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_no_start_scan.db")

_TS = "2026-01-01T00:00:00Z"


def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000", _TS, _TS),
    )
    conn.commit()
    conn.close()


def _seed_invite(lead_id: str, sent_at: str | None = _TS) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_invites (id, lead_id, course_id, sent_at)"
        " VALUES (?, ?, ?, ?)",
        (f"inv-{lead_id}", lead_id, "FREE_INTRO_AI_V0", sent_at),
    )
    conn.commit()
    conn.close()


def _seed_progress_event(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO progress_events (id, lead_id, course_id, section, occurred_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (f"evt-{lead_id}", lead_id, "FREE_INTRO_AI_V0", "P1_S1", _TS),
    )
    conn.commit()
    conn.close()


class TestRunNoStartScan(unittest.TestCase):

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
        result = run_no_start_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["scan_name"], "NO_START_SCAN")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])
        self.assertEqual(result["limit_used"], 100)

    # ------------------------------------------------------------------
    # T2 — one qualifying no-start lead → count 1 and correct lead_id
    # ------------------------------------------------------------------
    def test_t2_one_no_start_lead(self):
        """T2: invite sent, no progress → count=1, correct lead_id in list."""
        _seed_lead("lead-a")
        _seed_invite("lead-a")
        result = run_no_start_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn("lead-a", result["lead_ids"])

    # ------------------------------------------------------------------
    # T3 — excluded once progress evidence exists
    # ------------------------------------------------------------------
    def test_t3_excluded_when_progress_exists(self):
        """T3: invite sent + progress event → excluded from results."""
        _seed_lead("lead-b")
        _seed_invite("lead-b")
        _seed_progress_event("lead-b")
        result = run_no_start_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])

    # ------------------------------------------------------------------
    # T4 — limit respected through the worker wrapper
    # ------------------------------------------------------------------
    def test_t4_limit_respected(self):
        """T4: three qualifying leads, limit=2 → count=2."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_invite(i)
        result = run_no_start_scan(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["lead_ids"]), 2)
        self.assertEqual(result["limit_used"], 2)


if __name__ == "__main__":
    unittest.main()
