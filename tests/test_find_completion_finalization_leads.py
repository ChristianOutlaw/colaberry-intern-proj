"""
tests/test_find_completion_finalization_leads.py

Unit tests for execution/scans/find_completion_finalization_leads.py.
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
from execution.scans.find_completion_finalization_leads import find_completion_finalization_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_completion_finalization_leads.db")

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


def _seed_course_state(
    lead_id: str,
    completion_pct: float,
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


def _seed_invite(lead_id: str, sent_at: str | None = _TS_CREATED) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_invites (id, lead_id, course_id, sent_at)"
        " VALUES (?, ?, ?, ?)",
        (f"inv-{lead_id}", lead_id, "FREE_INTRO_AI_V0", sent_at),
    )
    conn.commit()
    conn.close()


class TestFindCompletionFinalizationLeads(unittest.TestCase):

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
    # T1 — empty DB -> empty list
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_empty_list(self):
        """T1: no leads -> []."""
        result = find_completion_finalization_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — completed lead -> returned
    # ------------------------------------------------------------------
    def test_t2_completed_lead_returned(self):
        """T2: completion_pct=100, started_at set -> included."""
        _seed_lead("lead-a")
        _seed_course_state("lead-a", completion_pct=100.0)
        result = find_completion_finalization_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-a")
        self.assertEqual(result[0]["completion_pct"], 100.0)
        self.assertIn("started_at", result[0])
        self.assertIn("last_activity_at", result[0])
        self.assertIn("score", result[0])
        self.assertIsNone(result[0]["score"])
        self.assertIn("has_quiz_data", result[0])
        self.assertIsNone(result[0]["has_quiz_data"])
        self.assertIn("has_reflection_data", result[0])
        self.assertIsInstance(result[0]["has_reflection_data"], bool)
        self.assertFalse(result[0]["has_reflection_data"])

    # ------------------------------------------------------------------
    # T3 — incomplete lead -> excluded
    # ------------------------------------------------------------------
    def test_t3_incomplete_lead_excluded(self):
        """T3: completion_pct=50 -> excluded."""
        _seed_lead("lead-b")
        _seed_course_state("lead-b", completion_pct=50.0)
        result = find_completion_finalization_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T5 — completed lead with no invite -> invite_sent == False
    # ------------------------------------------------------------------
    def test_t5_no_invite_returns_invite_sent_false(self):
        """T5: completed lead, no invite row -> invite_sent=False."""
        _seed_lead("lead-c")
        _seed_course_state("lead-c", completion_pct=100.0)
        result = find_completion_finalization_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["invite_sent"])

    # ------------------------------------------------------------------
    # T6 — completed lead with sent invite -> invite_sent == True
    # ------------------------------------------------------------------
    def test_t6_sent_invite_returns_invite_sent_true(self):
        """T6: completed lead with sent invite -> invite_sent=True."""
        _seed_lead("lead-d")
        _seed_course_state("lead-d", completion_pct=100.0)
        _seed_invite("lead-d")
        result = find_completion_finalization_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["invite_sent"])

    # ------------------------------------------------------------------
    # T4 — limit respected
    # ------------------------------------------------------------------
    def test_t4_limit_respected(self):
        """T4: three completed leads, limit=2 -> two returned."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_course_state(i, completion_pct=100.0)
        result = find_completion_finalization_leads(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
