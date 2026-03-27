"""
tests/test_find_ready_for_booking_leads.py

Unit tests for execution/scans/find_ready_for_booking_leads.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
All tests inject a fixed _NOW datetime; datetime.now() is never called.
"""

import gc
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.leads.compute_hot_lead_signal import ACTIVITY_WINDOW_DAYS
from execution.scans.find_ready_for_booking_leads import find_ready_for_booking_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_ready_for_booking_leads.db")

# Fixed clock — same epoch used across test modules
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)

# Activity timestamps relative to _NOW
_RECENT     = (_NOW - timedelta(days=1)).isoformat()   # 1 day ago — within window
_AT_EDGE    = (_NOW - timedelta(days=ACTIVITY_WINDOW_DAYS)).isoformat()  # exactly at boundary
_STALE      = (_NOW - timedelta(days=ACTIVITY_WINDOW_DAYS + 1)).isoformat()  # 1 day past window

_TS_CREATED = "2026-01-01T00:00:00"
_TS_STARTED = "2026-02-01T00:00:00"


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
    last_activity_at: str | None = _RECENT,
    started_at: str | None = _TS_STARTED,
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


class TestFindReadyForBookingLeads(unittest.TestCase):

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
    # T1 — empty DB → empty list
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_empty_list(self):
        """T1: no leads → []."""
        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T2 — lead meets all conditions → included
    # ------------------------------------------------------------------
    def test_t2_booking_ready_lead_returned(self):
        """T2 (Scenario A): completed + invite sent + recent activity → included."""
        _seed_lead("L-book")
        _seed_course_state("L-book", completion_pct=100.0, last_activity_at=_RECENT)
        _seed_invite("L-book")

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "L-book")
        self.assertEqual(result[0]["completion_pct"], 100.0)
        self.assertIn("last_activity_at", result[0])

    # ------------------------------------------------------------------
    # T3 — incomplete lead → excluded
    # ------------------------------------------------------------------
    def test_t3_incomplete_lead_excluded(self):
        """T3 (Scenario B): completion_pct=50, invite sent, recent activity → not included."""
        _seed_lead("L-partial")
        _seed_course_state("L-partial", completion_pct=50.0, last_activity_at=_RECENT)
        _seed_invite("L-partial")

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T4 — no sent invite → excluded
    # ------------------------------------------------------------------
    def test_t4_no_invite_excluded(self):
        """T4 (Scenario B): completed, recent activity, but no invite sent → not included."""
        _seed_lead("L-noinv")
        _seed_course_state("L-noinv", completion_pct=100.0, last_activity_at=_RECENT)
        # no invite row seeded

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    def test_t4b_unsent_invite_excluded(self):
        """T4b: completed + invite row exists but sent_at IS NULL → not included."""
        _seed_lead("L-unsent")
        _seed_course_state("L-unsent", completion_pct=100.0, last_activity_at=_RECENT)
        _seed_invite("L-unsent", sent_at=None)

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T5 — activity outside recency window → excluded
    # ------------------------------------------------------------------
    def test_t5_stale_activity_excluded(self):
        """T5 (Scenario B): completed + invite sent, but activity outside 7-day window → not included."""
        _seed_lead("L-stale")
        _seed_course_state("L-stale", completion_pct=100.0, last_activity_at=_STALE)
        _seed_invite("L-stale")

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T6 — activity exactly at boundary → included (>= not >)
    # ------------------------------------------------------------------
    def test_t6_activity_at_edge_included(self):
        """T6: last_activity_at == cutoff exactly → included (boundary is inclusive)."""
        _seed_lead("L-edge")
        _seed_course_state("L-edge", completion_pct=100.0, last_activity_at=_AT_EDGE)
        _seed_invite("L-edge")

        result = find_ready_for_booking_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "L-edge")

    # ------------------------------------------------------------------
    # T7 — limit respected
    # ------------------------------------------------------------------
    def test_t7_limit_respected(self):
        """T7: three booking-ready leads, limit=2 → two returned."""
        for lid in ("L-a", "L-b", "L-c"):
            _seed_lead(lid)
            _seed_course_state(lid, completion_pct=100.0, last_activity_at=_RECENT)
            _seed_invite(lid)

        result = find_ready_for_booking_leads(now=_NOW, limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)

    # ------------------------------------------------------------------
    # T8 — raises ValueError when now is None
    # ------------------------------------------------------------------
    def test_t8_raises_on_none_now(self):
        """T8: now=None → ValueError (determinism guard)."""
        with self.assertRaises(ValueError):
            find_ready_for_booking_leads(now=None, db_path=TEST_DB_PATH)


if __name__ == "__main__":
    unittest.main()
