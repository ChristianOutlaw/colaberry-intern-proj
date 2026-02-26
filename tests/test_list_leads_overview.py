"""
tests/test_list_leads_overview.py

Unit tests for execution/leads/list_leads_overview.py.

Fast, deterministic, no network access.  Each test gets a fresh isolated
SQLite file that is removed in tearDown.  All timestamps are fixed ISO strings
so ordering and HOT-signal assertions are fully deterministic.

Schema assumptions under test:
    leads           — base table (id, name, email, phone)
    course_invites  — MAX(sent_at) per lead joined in; nullable per lead
    course_state    — completion_pct, current_section, last_activity_at; nullable

Fixed reference point:
    _NOW    = 2026-02-25 12:00 UTC
    cutoff  = _NOW - 7 days = 2026-02-18 12:00 UTC

Tests:
    a) test_empty_db_returns_empty_list       — no leads → []
    b) test_invited_vs_cold_lead              — 2 leads; only invited lead has
                                               invited_sent_at; cold has NULLs
    c) test_ordering_by_last_activity_at      — newer activity first; NULL last
    d) test_limit_applied                     — limit=2 with 3 leads → 2 rows
    e) test_max_limit_constant_is_1000        — MAX_LIMIT sentinel value check
    f) test_return_type_is_list_of_dict       — each row is dict with all keys
                                               including is_hot
    g) test_latest_invite_used_when_multiple  — MAX(sent_at) chosen
    h) test_requires_explicit_now             — ValueError when now=None
    i) test_is_hot_hot_lead                   — invited + pct>=25 + recent → 1
    j) test_is_hot_stale_lead                 — invited + pct>=25 + old → 0
    k) test_is_hot_not_invited                — progress but no invite → 0
    l) test_is_hot_below_threshold            — invited + pct<25 + recent → 0
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern used across all test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.leads.list_leads_overview import (                             # noqa: E402
    HOT_COMPLETION_THRESHOLD,
    HOT_RECENCY_DAYS,
    MAX_LIMIT,
    list_leads_overview,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_list_leads_overview.db")

# ---------------------------------------------------------------------------
# Fixed reference "now" — never call datetime.now() in tests.
# cutoff = _NOW - 7 days = 2026-02-18T12:00:00+00:00
# ---------------------------------------------------------------------------
_NOW     = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
_TS_CREATED = "2026-01-01T00:00:00+00:00"

# Activity timestamps relative to _NOW / cutoff (2026-02-18T12:00:00+00:00)
_ACT_NEWER  = "2026-02-25T12:00:00+00:00"   # same day as _NOW   → within 7 days
_ACT_OLDER  = "2026-02-20T08:00:00+00:00"   # 5 days before _NOW → within 7 days
_ACT_HOT    = "2026-02-22T12:00:00+00:00"   # 3 days before _NOW → within 7 days
_ACT_STALE  = "2026-02-10T12:00:00+00:00"   # 15 days before _NOW → outside 7 days

# Invite timestamps
_INV_TS_1 = "2026-02-10T09:00:00+00:00"
_INV_TS_2 = "2026-02-15T09:00:00+00:00"  # later of two invites


# ---------------------------------------------------------------------------
# Seeding helpers — each opens its own connection, commits, then closes.
# ---------------------------------------------------------------------------
def _seed_lead(lead_id: str, name: str | None = None,
               email: str | None = None, phone: str | None = None) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, name, email, phone, _TS_CREATED, _TS_CREATED),
    )
    conn.commit()
    conn.close()


def _seed_invite(invite_id: str, lead_id: str, sent_at: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_invites (id, lead_id, sent_at) VALUES (?, ?, ?)",
        (invite_id, lead_id, sent_at),
    )
    conn.commit()
    conn.close()


def _seed_course_state(lead_id: str, completion_pct: float,
                       current_section: str, last_activity_at: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state"
        " (lead_id, completion_pct, current_section, last_activity_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (lead_id, completion_pct, current_section, last_activity_at, _TS_CREATED),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------
class TestListLeadsOverview(unittest.TestCase):

    def setUp(self) -> None:
        """Create a fresh, schema-initialised DB before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        """Remove the isolated test DB after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # a) Empty DB returns []
    # ------------------------------------------------------------------
    def test_empty_db_returns_empty_list(self) -> None:
        """No leads in DB must return an empty list, not None or an error."""
        result = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    # ------------------------------------------------------------------
    # b) Invited lead vs cold lead — field population + is_hot values
    # ------------------------------------------------------------------
    def test_invited_vs_cold_lead(self) -> None:
        """Invited lead has invited_sent_at + course fields + is_hot=1;
        cold lead has NULLs and is_hot=0."""
        _seed_lead("LEAD_INVITED", name="Alice", email="alice@example.com", phone="555-0100")
        _seed_lead("LEAD_COLD")
        _seed_invite("inv-001", "LEAD_INVITED", _INV_TS_1)
        # LEAD_INVITED: invited, 33.33% >= 25%, last_activity within 7 days → HOT
        _seed_course_state("LEAD_INVITED", 33.33, "P1_S3", _ACT_NEWER)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 2)

        by_id = {r["lead_id"]: r for r in rows}

        # Invited lead — all fields populated; is_hot=1.
        inv = by_id["LEAD_INVITED"]
        self.assertEqual(inv["name"], "Alice")
        self.assertEqual(inv["email"], "alice@example.com")
        self.assertEqual(inv["phone"], "555-0100")
        self.assertEqual(inv["invited_sent_at"], _INV_TS_1)
        self.assertAlmostEqual(inv["completion_pct"], 33.33, places=2)
        self.assertEqual(inv["current_section"], "P1_S3")
        self.assertEqual(inv["last_activity_at"], _ACT_NEWER)
        self.assertEqual(inv["is_hot"], 1, "Invited + ≥25% + recent → should be HOT")

        # Cold lead — join columns all NULL; is_hot=0.
        cold = by_id["LEAD_COLD"]
        self.assertIsNone(cold["invited_sent_at"])
        self.assertIsNone(cold["completion_pct"])
        self.assertIsNone(cold["current_section"])
        self.assertIsNone(cold["last_activity_at"])
        self.assertEqual(cold["is_hot"], 0, "Cold lead with no invite should not be HOT")

    # ------------------------------------------------------------------
    # c) Ordering: newer last_activity_at first; NULL activity last
    # ------------------------------------------------------------------
    def test_ordering_by_last_activity_at(self) -> None:
        """Rows must be ordered by last_activity_at DESC NULLS LAST."""
        _seed_lead("LEAD_NEWER")
        _seed_lead("LEAD_OLDER")
        _seed_lead("LEAD_NULL")   # no course_state → last_activity_at IS NULL

        _seed_course_state("LEAD_NEWER", 100.0, "P3_S3", _ACT_NEWER)
        _seed_course_state("LEAD_OLDER", 50.0,  "P2_S1", _ACT_OLDER)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 3)

        lead_ids = [r["lead_id"] for r in rows]
        self.assertEqual(
            lead_ids[0], "LEAD_NEWER",
            f"Most recent activity should be first; got {lead_ids}",
        )
        self.assertEqual(
            lead_ids[1], "LEAD_OLDER",
            f"Older activity should be second; got {lead_ids}",
        )
        self.assertEqual(
            lead_ids[2], "LEAD_NULL",
            f"NULL activity should be last (NULLS LAST); got {lead_ids}",
        )

    # ------------------------------------------------------------------
    # d) limit applied
    # ------------------------------------------------------------------
    def test_limit_applied(self) -> None:
        """limit=2 must return exactly 2 rows when 3 leads exist."""
        for i in range(3):
            _seed_lead(f"LEAD_{i:02d}")

        rows = list_leads_overview(db_path=TEST_DB_PATH, limit=2, now=_NOW)
        self.assertEqual(len(rows), 2, f"Expected 2 rows with limit=2, got {len(rows)}")

    # ------------------------------------------------------------------
    # e) MAX_LIMIT constant value
    # ------------------------------------------------------------------
    def test_max_limit_constant_is_1000(self) -> None:
        """MAX_LIMIT sentinel must equal 1000."""
        self.assertEqual(MAX_LIMIT, 1000)

    # ------------------------------------------------------------------
    # f) Return type — list of dict with all expected keys including is_hot
    # ------------------------------------------------------------------
    def test_return_type_is_list_of_dict(self) -> None:
        """Each element in the result must be a dict with all expected keys."""
        _seed_lead("LEAD_TYPE_CHECK")

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertIsInstance(row, dict)

        expected_keys = {
            "lead_id", "name", "email", "phone",
            "invited_sent_at", "completion_pct", "current_section",
            "last_activity_at", "is_hot",
        }
        missing = expected_keys - row.keys()
        self.assertFalse(missing, f"Row dict is missing keys: {missing}")

    # ------------------------------------------------------------------
    # g) Latest invite chosen when a lead has multiple invite rows
    # ------------------------------------------------------------------
    def test_latest_invite_used_when_multiple(self) -> None:
        """MAX(sent_at) must be used when a lead has more than one course invite."""
        _seed_lead("LEAD_MULTI_INV")
        _seed_invite("inv-early", "LEAD_MULTI_INV", _INV_TS_1)
        _seed_invite("inv-late",  "LEAD_MULTI_INV", _INV_TS_2)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["invited_sent_at"], _INV_TS_2,
            "Must return the LATEST invite sent_at, not the first inserted.",
        )

    # ------------------------------------------------------------------
    # h) now=None raises ValueError
    # ------------------------------------------------------------------
    def test_requires_explicit_now(self) -> None:
        """Calling without now must raise ValueError, not silently use datetime.now()."""
        with self.assertRaises(ValueError):
            list_leads_overview(db_path=TEST_DB_PATH, now=None)

    # ------------------------------------------------------------------
    # i) is_hot = 1: invited + completion >= 25% + activity within 7 days
    # ------------------------------------------------------------------
    def test_is_hot_hot_lead(self) -> None:
        """Lead with invite, pct >= 25, recent activity must have is_hot=1."""
        _seed_lead("HOT_LEAD")
        _seed_invite("inv-hot", "HOT_LEAD", _INV_TS_1)
        _seed_course_state("HOT_LEAD", 33.33, "P1_S3", _ACT_HOT)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["is_hot"], 1,
            "Invited + ≥25% completion + activity within 7 days → must be HOT (is_hot=1)",
        )

    # ------------------------------------------------------------------
    # j) is_hot = 0: invited + completion >= 25% BUT activity older than 7 days
    # ------------------------------------------------------------------
    def test_is_hot_stale_lead(self) -> None:
        """Lead with invite and pct >= 25 but stale activity must have is_hot=0."""
        _seed_lead("STALE_LEAD")
        _seed_invite("inv-stale", "STALE_LEAD", _INV_TS_1)
        _seed_course_state("STALE_LEAD", 33.33, "P1_S3", _ACT_STALE)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["is_hot"], 0,
            f"Activity at {_ACT_STALE} is older than 7 days from _NOW — must not be HOT",
        )

    # ------------------------------------------------------------------
    # k) is_hot = 0: progress exists but no invite sent
    # ------------------------------------------------------------------
    def test_is_hot_not_invited(self) -> None:
        """Lead with progress but no invite must have is_hot=0 (invite is required)."""
        _seed_lead("NO_INVITE_LEAD")
        _seed_course_state("NO_INVITE_LEAD", 50.0, "P2_S1", _ACT_HOT)
        # No invite seeded.

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["is_hot"], 0,
            "No invite sent → must not be HOT even with good progress and recent activity",
        )

    # ------------------------------------------------------------------
    # l) is_hot = 0: invited + recent BUT completion < 25%
    # ------------------------------------------------------------------
    def test_is_hot_below_threshold(self) -> None:
        """Lead with invite and recent activity but completion < 25% must have is_hot=0."""
        _seed_lead("LOW_PCT_LEAD")
        _seed_invite("inv-low", "LOW_PCT_LEAD", _INV_TS_1)
        _seed_course_state("LOW_PCT_LEAD", 11.11, "P1_S1", _ACT_HOT)

        rows = list_leads_overview(db_path=TEST_DB_PATH, now=_NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["is_hot"], 0,
            f"Completion 11.11% < {HOT_COMPLETION_THRESHOLD}% → must not be HOT",
        )


if __name__ == "__main__":
    unittest.main()
