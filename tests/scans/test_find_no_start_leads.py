"""
tests/test_find_no_start_leads.py

Unit tests for execution/scans/find_no_start_leads.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.scans.find_no_start_leads import find_no_start_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_no_start_leads.db")

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


def _seed_course_state(lead_id: str, started_at: str | None = _TS) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state (lead_id, course_id, started_at, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (lead_id, "FREE_INTRO_AI_V0", started_at, _TS),
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


class TestFindNoStartLeads(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()  # release SQLite connections before file removal (Windows)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — empty DB returns empty list
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_empty_list(self):
        """T1: no leads in DB → empty result."""
        result = find_no_start_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — lead with sent invite and no progress is returned
    # ------------------------------------------------------------------
    def test_t2_invited_no_progress_is_returned(self):
        """T2: invite sent, no course_state, no progress_events → included."""
        _seed_lead("lead-a")
        _seed_invite("lead-a")
        result = find_no_start_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-a")
        self.assertIn("no_start_threshold", result[0])
        threshold = result[0]["no_start_threshold"]
        self.assertTrue(
            threshold is None or threshold in ("NO_START_24H", "NO_START_72H", "NO_START_7D"),
            f"unexpected threshold value: {threshold!r}",
        )

    # ------------------------------------------------------------------
    # T3a — excluded once course_state.started_at is set
    # ------------------------------------------------------------------
    def test_t3a_excluded_when_course_state_started(self):
        """T3a: invite sent + course_state.started_at IS NOT NULL → excluded."""
        _seed_lead("lead-b")
        _seed_invite("lead-b")
        _seed_course_state("lead-b", started_at=_TS)
        result = find_no_start_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T3b — excluded once progress_events exist
    # ------------------------------------------------------------------
    def test_t3b_excluded_when_progress_events_exist(self):
        """T3b: invite sent + progress_events row → excluded."""
        _seed_lead("lead-c")
        _seed_invite("lead-c")
        _seed_progress_event("lead-c")
        result = find_no_start_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T4 — limit is respected
    # ------------------------------------------------------------------
    def test_t4_limit_is_respected(self):
        """T4: three qualifying leads, limit=2 → only 2 returned."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_invite(i)
        result = find_no_start_leads(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
