"""
tests/test_find_unsent_invite_leads.py

Unit tests for execution/scans/find_unsent_invite_leads.py.
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
from execution.scans.find_unsent_invite_leads import find_unsent_invite_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_unsent_invite_leads.db")

_TS = "2026-01-01T00:00:00Z"


def _seed_lead(lead_id: str, name: str = "Test Lead") -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, name, f"{lead_id}@test.com", "5550000000", _TS, _TS),
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


class TestFindUnsentInviteLeads(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()  # release SQLite connections held by CPython before file removal (Windows)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — empty DB returns empty list
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_empty_list(self):
        """T1: no leads in DB → empty result."""
        result = find_unsent_invite_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — lead with no invite is returned
    # ------------------------------------------------------------------
    def test_t2_lead_without_invite_is_returned(self):
        """T2: lead exists with no course_invites row → included in results."""
        _seed_lead("lead-a")
        result = find_unsent_invite_leads(db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "lead-a")

    # ------------------------------------------------------------------
    # T3 — lead with confirmed invite is excluded
    # ------------------------------------------------------------------
    def test_t3_lead_with_sent_invite_excluded(self):
        """T3: lead with sent_at IS NOT NULL → excluded from results."""
        _seed_lead("lead-b")
        _seed_invite("lead-b", sent_at=_TS)
        result = find_unsent_invite_leads(db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T4 — limit is respected
    # ------------------------------------------------------------------
    def test_t4_limit_is_respected(self):
        """T4: three unsent leads exist, limit=2 → only 2 returned."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
        result = find_unsent_invite_leads(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
