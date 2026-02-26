"""
tests/test_list_leads_overview.py

Unit tests for execution/leads/list_leads_overview.py.

Fast, deterministic, no network access.  Each test gets a fresh isolated
SQLite file that is removed in tearDown.  All timestamps are fixed ISO strings
so ordering assertions are fully deterministic.

Schema assumptions under test:
    leads           — base table (id, name, email, phone)
    course_invites  — MAX(sent_at) per lead joined in; nullable per lead
    course_state    — completion_pct, current_section, last_activity_at; nullable

Tests:
    a) test_empty_db_returns_empty_list       — no leads → []
    b) test_invited_vs_cold_lead              — 2 leads; only invited has invited_sent_at
    c) test_ordering_by_last_activity_at      — newer activity first; NULL activity last
    d) test_limit_applied                     — limit=2 with 3 leads → exactly 2 rows
    e) test_max_limit_constant_is_1000        — MAX_LIMIT sentinel value check
    f) test_return_type_is_list_of_dict       — each row is a dict with expected keys
    g) test_latest_invite_used_when_multiple  — MAX(sent_at) chosen among duplicate invites
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern used across all test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.leads.list_leads_overview import MAX_LIMIT, list_leads_overview  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_list_leads_overview.db")

# ---------------------------------------------------------------------------
# Fixed timestamps — never use datetime.now in tests.
# ---------------------------------------------------------------------------
_TS_CREATED = "2026-01-01T00:00:00+00:00"
_ACT_NEWER  = "2026-02-25T12:00:00+00:00"
_ACT_OLDER  = "2026-02-20T08:00:00+00:00"
_INV_TS_1   = "2026-02-10T09:00:00+00:00"
_INV_TS_2   = "2026-02-15T09:00:00+00:00"  # later of two invites


# ---------------------------------------------------------------------------
# Seeding helpers — open their own connection, commit, then close.
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
        result = list_leads_overview(db_path=TEST_DB_PATH)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    # ------------------------------------------------------------------
    # b) Invited lead vs cold lead — field population
    # ------------------------------------------------------------------
    def test_invited_vs_cold_lead(self) -> None:
        """Invited lead has invited_sent_at + course fields; cold lead has NULLs."""
        _seed_lead("LEAD_INVITED", name="Alice", email="alice@example.com", phone="555-0100")
        _seed_lead("LEAD_COLD")
        _seed_invite("inv-001", "LEAD_INVITED", _INV_TS_1)
        _seed_course_state("LEAD_INVITED", 33.33, "P1_S3", _ACT_NEWER)

        rows = list_leads_overview(db_path=TEST_DB_PATH)
        self.assertEqual(len(rows), 2)

        by_id = {r["lead_id"]: r for r in rows}

        # Invited lead — all fields populated.
        inv = by_id["LEAD_INVITED"]
        self.assertEqual(inv["name"], "Alice")
        self.assertEqual(inv["email"], "alice@example.com")
        self.assertEqual(inv["phone"], "555-0100")
        self.assertEqual(inv["invited_sent_at"], _INV_TS_1)
        self.assertAlmostEqual(inv["completion_pct"], 33.33, places=2)
        self.assertEqual(inv["current_section"], "P1_S3")
        self.assertEqual(inv["last_activity_at"], _ACT_NEWER)

        # Cold lead — join columns all NULL.
        cold = by_id["LEAD_COLD"]
        self.assertIsNone(cold["invited_sent_at"])
        self.assertIsNone(cold["completion_pct"])
        self.assertIsNone(cold["current_section"])
        self.assertIsNone(cold["last_activity_at"])

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

        rows = list_leads_overview(db_path=TEST_DB_PATH)
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

        rows = list_leads_overview(db_path=TEST_DB_PATH, limit=2)
        self.assertEqual(len(rows), 2, f"Expected 2 rows with limit=2, got {len(rows)}")

    # ------------------------------------------------------------------
    # e) MAX_LIMIT constant value
    # ------------------------------------------------------------------
    def test_max_limit_constant_is_1000(self) -> None:
        """MAX_LIMIT sentinel must equal 1000."""
        self.assertEqual(MAX_LIMIT, 1000)

    # ------------------------------------------------------------------
    # f) Return type — list of dict with expected keys
    # ------------------------------------------------------------------
    def test_return_type_is_list_of_dict(self) -> None:
        """Each element in the result must be a dict with all expected keys."""
        _seed_lead("LEAD_TYPE_CHECK")

        rows = list_leads_overview(db_path=TEST_DB_PATH)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertIsInstance(row, dict)

        expected_keys = {
            "lead_id", "name", "email", "phone",
            "invited_sent_at", "completion_pct", "current_section", "last_activity_at",
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

        rows = list_leads_overview(db_path=TEST_DB_PATH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["invited_sent_at"], _INV_TS_2,
            "Must return the LATEST invite sent_at, not the first inserted.",
        )


if __name__ == "__main__":
    unittest.main()
