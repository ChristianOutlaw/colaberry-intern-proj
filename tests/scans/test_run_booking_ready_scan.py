"""
tests/test_run_booking_ready_scan.py

Unit tests for execution/orchestration/run_booking_ready_scan.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
All tests inject a fixed _NOW datetime; datetime.now() is never called.
"""

import gc
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                         # noqa: E402
from execution.decision.build_cora_recommendation import (               # noqa: E402
    EVENT_HOT_BOOKING,
    PRIORITY_HIGH,
)
from execution.orchestration.run_booking_ready_scan import (             # noqa: E402
    run_booking_ready_scan,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_booking_ready_scan.db")

_NOW        = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
_RECENT     = (_NOW - timedelta(days=1)).isoformat()   # within 7-day HOT window
_STALE      = (_NOW - timedelta(days=8)).isoformat()   # outside HOT window
_TS_CREATED = "2026-01-01T00:00:00"
_TS_STARTED = "2026-02-01T00:00:00"


# ---------------------------------------------------------------------------
# Seed helpers (mirror test_find_ready_for_booking_leads.py pattern)
# ---------------------------------------------------------------------------

def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000",
         _TS_CREATED, _TS_CREATED),
    )
    conn.commit()
    conn.close()


def _seed_course_state(
    lead_id: str,
    completion_pct: float,
    last_activity_at: str = _RECENT,
    started_at: str = _TS_STARTED,
) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state"
        " (lead_id, course_id, started_at, completion_pct, last_activity_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "FREE_INTRO_AI_V0", started_at, completion_pct,
         last_activity_at, _TS_CREATED),
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


def _seed_booking_ready(lead_id: str) -> None:
    """Seed a lead that satisfies all READY_FOR_BOOKING conditions."""
    _seed_lead(lead_id)
    _seed_course_state(lead_id, completion_pct=100.0, last_activity_at=_RECENT)
    _seed_invite(lead_id)


class TestRunBookingReadyScan(unittest.TestCase):

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
    # Scenario B — no eligible leads → empty list
    # ------------------------------------------------------------------
    def test_scenario_b_no_leads_returns_empty(self):
        """Scenario B: empty DB → []."""
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    def test_scenario_b_ineligible_lead_returns_empty(self):
        """Scenario B: lead fails scan conditions (stale activity) → []."""
        _seed_lead("L-stale")
        _seed_course_state("L-stale", completion_pct=100.0, last_activity_at=_STALE)
        _seed_invite("L-stale")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Scenario A — one eligible lead → one recommendation
    # ------------------------------------------------------------------
    def test_scenario_a_one_lead_one_recommendation(self):
        """Scenario A: one booking-ready lead → one result."""
        _seed_booking_ready("L-book")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 1)

    def test_scenario_a_result_has_required_keys(self):
        """Scenario A: result dict has lead_id, event_type, priority, reason_codes."""
        _seed_booking_ready("L-shape")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        rec = result[0]
        for key in ("lead_id", "event_type", "priority", "reason_codes"):
            self.assertIn(key, rec, f"Missing key: {key}")

    def test_scenario_a_event_type_is_ready_for_booking(self):
        """Scenario A: HOT + complete lead → event_type == READY_FOR_BOOKING."""
        _seed_booking_ready("L-hot")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result[0]["event_type"], EVENT_HOT_BOOKING)

    def test_scenario_a_priority_is_high(self):
        """Scenario A: READY_FOR_BOOKING recommendation → priority == HIGH."""
        _seed_booking_ready("L-prio")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result[0]["priority"], PRIORITY_HIGH)

    def test_scenario_a_lead_id_matches(self):
        """Scenario A: lead_id in result matches the seeded lead."""
        _seed_booking_ready("L-id-check")
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(result[0]["lead_id"], "L-id-check")

    # ------------------------------------------------------------------
    # Scenario C — multiple leads → multiple recommendations
    # ------------------------------------------------------------------
    def test_scenario_c_multiple_leads_multiple_recommendations(self):
        """Scenario C: three booking-ready leads → three results."""
        for lid in ("L-1", "L-2", "L-3"):
            _seed_booking_ready(lid)
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 3)

    def test_scenario_c_all_results_are_ready_for_booking(self):
        """Scenario C: every result has event_type == READY_FOR_BOOKING."""
        for lid in ("L-a", "L-b", "L-c"):
            _seed_booking_ready(lid)
        result = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)
        for rec in result:
            self.assertEqual(rec["event_type"], EVENT_HOT_BOOKING)

    def test_scenario_c_limit_respected(self):
        """Scenario C: five leads, limit=2 → two results."""
        for lid in ("L-x1", "L-x2", "L-x3", "L-x4", "L-x5"):
            _seed_booking_ready(lid)
        result = run_booking_ready_scan(now=_NOW, limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
