"""
tests/test_find_warm_review_leads.py

Unit tests for execution/scans/find_warm_review_leads.py.
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
from execution.decision.build_cora_recommendation import STALL_DAYS
from execution.leads.compute_hot_lead_signal import ACTIVITY_WINDOW_DAYS
from execution.scans.find_warm_review_leads import find_warm_review_leads

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_find_warm_review_leads.db")

# Fixed clock — same epoch used across test modules
_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)

# Activity timestamp helpers (days relative to _NOW)
def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()

# Named timestamps for each zone
_HOT_ZONE        = _iso(1)                         # 1 day ago  — inside 7-day HOT window → BOOKING_READY
_WARM_ZONE       = _iso(ACTIVITY_WINDOW_DAYS + 1)  # 8 days ago — in WARM_REVIEW band
_STALE_ZONE      = _iso(STALL_DAYS + 1)            # 15 days ago — past 14-day threshold → REENGAGE_COMPLETED
_TS_CREATED      = "2026-01-01T00:00:00"
_TS_STARTED      = "2026-02-01T00:00:00"


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
    last_activity_at: str | None = _WARM_ZONE,
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


class TestFindWarmReviewLeads(unittest.TestCase):

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
        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Scenario A — completed, invite sent, activity in WARM band → included
    # ------------------------------------------------------------------
    def test_scenario_a_warm_review_lead_included(self):
        """Scenario A: completed, invite sent, activity 8 days ago → included."""
        _seed_lead("L-warm")
        _seed_course_state("L-warm", completion_pct=100.0, last_activity_at=_WARM_ZONE)
        _seed_invite("L-warm")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lead_id"], "L-warm")
        self.assertEqual(result[0]["completion_pct"], 100.0)

    def test_scenario_a_null_activity_included(self):
        """Scenario A (NULL): completed, invite sent, no activity timestamp → included."""
        _seed_lead("L-null")
        _seed_course_state("L-null", completion_pct=100.0, last_activity_at=None)
        _seed_invite("L-null")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        ids = [r["lead_id"] for r in result]
        self.assertIn("L-null", ids)

    # ------------------------------------------------------------------
    # Scenario B — HOT / booking-ready → excluded
    # ------------------------------------------------------------------
    def test_scenario_b_hot_activity_excluded(self):
        """Scenario B: completed + activity within 7-day HOT window → not included."""
        _seed_lead("L-hot")
        _seed_course_state("L-hot", completion_pct=100.0, last_activity_at=_HOT_ZONE)
        _seed_invite("L-hot")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Scenario C — stale enough for REENGAGE_COMPLETED → excluded
    # ------------------------------------------------------------------
    def test_scenario_c_stale_activity_excluded(self):
        """Scenario C: completed + activity older than STALL_DAYS → not included."""
        _seed_lead("L-stale")
        _seed_course_state("L-stale", completion_pct=100.0, last_activity_at=_STALE_ZONE)
        _seed_invite("L-stale")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Scenario D — invite not sent OR incomplete → excluded
    # ------------------------------------------------------------------
    def test_scenario_d_no_invite_row_excluded(self):
        """Scenario D: completed, no invite row at all → not included."""
        _seed_lead("L-noinv")
        _seed_course_state("L-noinv", completion_pct=100.0, last_activity_at=_WARM_ZONE)

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    def test_scenario_d_unsent_invite_excluded(self):
        """Scenario D: completed + invite row exists but sent_at IS NULL → not included."""
        _seed_lead("L-unsent")
        _seed_course_state("L-unsent", completion_pct=100.0, last_activity_at=_WARM_ZONE)
        _seed_invite("L-unsent", sent_at=None)

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    def test_scenario_d_incomplete_excluded(self):
        """Scenario D: invite sent + recent activity but completion < 100 → not included."""
        _seed_lead("L-partial")
        _seed_course_state("L-partial", completion_pct=75.0, last_activity_at=_WARM_ZONE)
        _seed_invite("L-partial")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T — boundary: activity exactly at HOT cutoff → excluded (is HOT)
    # ------------------------------------------------------------------
    def test_boundary_at_hot_cutoff_excluded(self):
        """Activity exactly at now - 7 days is still HOT → not included in WARM_REVIEW."""
        _seed_lead("L-hotedge")
        _seed_course_state("L-hotedge", completion_pct=100.0,
                           last_activity_at=_iso(ACTIVITY_WINDOW_DAYS))
        _seed_invite("L-hotedge")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # T — boundary: activity exactly at stale cutoff → included (not yet stale)
    # ------------------------------------------------------------------
    def test_boundary_at_stale_cutoff_included(self):
        """Activity exactly at now - 14 days is not yet stale → included in WARM_REVIEW."""
        _seed_lead("L-staleedge")
        _seed_course_state("L-staleedge", completion_pct=100.0,
                           last_activity_at=_iso(STALL_DAYS))
        _seed_invite("L-staleedge")

        result = find_warm_review_leads(now=_NOW, db_path=TEST_DB_PATH)
        ids = [r["lead_id"] for r in result]
        self.assertIn("L-staleedge", ids)

    # ------------------------------------------------------------------
    # T — limit respected
    # ------------------------------------------------------------------
    def test_limit_respected(self):
        """Three warm-review leads, limit=2 → two returned."""
        for lid in ("L-a", "L-b", "L-c"):
            _seed_lead(lid)
            _seed_course_state(lid, completion_pct=100.0, last_activity_at=_WARM_ZONE)
            _seed_invite(lid)

        result = find_warm_review_leads(now=_NOW, limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)

    # ------------------------------------------------------------------
    # T — raises ValueError when now is None
    # ------------------------------------------------------------------
    def test_raises_on_none_now(self):
        """now=None → ValueError (determinism guard)."""
        with self.assertRaises(ValueError):
            find_warm_review_leads(now=None, db_path=TEST_DB_PATH)


if __name__ == "__main__":
    unittest.main()
