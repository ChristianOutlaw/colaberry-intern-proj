"""
tests/test_list_sync_records.py

Unit tests for execution/leads/list_sync_records.py.

Fast, deterministic, no network access.  Uses an isolated SQLite file
cleaned up after each test.  All fixture data uses fixed ISO timestamps
so ordering assertions are deterministic.

Fixture: 4 sync_records across 2 leads.

    Lead A — NEEDS_SYNC  updated_at="2026-02-25T12:00:00+00:00"  (newest)
    Lead A — SENT        updated_at="2026-02-24T12:00:00+00:00"
    Lead B — FAILED      updated_at="2026-02-23T12:00:00+00:00"
    Lead B — NEEDS_SYNC  updated_at="2026-02-22T12:00:00+00:00"  (oldest)

Tests:
    a) no_filters_returns_sorted_desc   — all 4 rows, strictly descending updated_at
    b) status_filter                    — status=NEEDS_SYNC → 2 rows
    c) lead_id_filter                   — lead_id=LEAD_A   → 2 rows
    d) status_and_lead_id_filter        — both filters      → 1 row
    e) limit_applied                    — limit=2           → 2 rows
    f) empty_result                     — status=PENDING (no rows) → []
    g) return_type_is_list_of_dict      — each element is a dict with expected keys
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern as all other test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db          # noqa: E402
from execution.leads.list_sync_records import list_sync_records  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_list_sync_records.db")

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------
LEAD_A = "LEAD_A"
LEAD_B = "LEAD_B"

_TS_LEAD_CREATED = "2026-01-01T00:00:00+00:00"

# sync_records fixture rows — (lead_id, destination, status, created_at, updated_at)
_SYNC_ROWS = [
    (LEAD_A, "GHL", "NEEDS_SYNC", "2026-02-25T12:00:00+00:00", "2026-02-25T12:00:00+00:00"),
    (LEAD_A, "GHL", "SENT",       "2026-02-24T12:00:00+00:00", "2026-02-24T12:00:00+00:00"),
    (LEAD_B, "GHL", "FAILED",     "2026-02-23T12:00:00+00:00", "2026-02-23T12:00:00+00:00"),
    (LEAD_B, "GHL", "NEEDS_SYNC", "2026-02-22T12:00:00+00:00", "2026-02-22T12:00:00+00:00"),
]


class TestListSyncRecords(unittest.TestCase):

    def setUp(self):
        """Create and seed the isolated test database."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)
        init_db(self.conn)

        # Seed leads (FK prerequisite).
        for lead_id in (LEAD_A, LEAD_B):
            self.conn.execute(
                "INSERT INTO leads (id, created_at, updated_at) VALUES (?, ?, ?)",
                (lead_id, _TS_LEAD_CREATED, _TS_LEAD_CREATED),
            )

        # Seed sync_records.
        for lead_id, destination, status, created_at, updated_at in _SYNC_ROWS:
            self.conn.execute(
                """
                INSERT INTO sync_records
                    (lead_id, destination, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lead_id, destination, status, created_at, updated_at),
            )

        self.conn.commit()
        self.conn.close()

    def tearDown(self):
        """Remove the isolated test database."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # a) No filters — all 4 rows, strictly descending updated_at
    # ------------------------------------------------------------------
    def test_no_filters_returns_sorted_desc(self):
        """No filters must return all 4 rows ordered by updated_at DESC."""
        rows = list_sync_records(db_path=TEST_DB_PATH)

        self.assertEqual(len(rows), 4, f"Expected 4 rows, got {len(rows)}")

        updated_ats = [r["updated_at"] for r in rows]
        for i in range(len(updated_ats) - 1):
            self.assertGreater(
                updated_ats[i], updated_ats[i + 1],
                f"Row {i} updated_at ({updated_ats[i]!r}) must be > row {i+1} ({updated_ats[i+1]!r})",
            )

    # ------------------------------------------------------------------
    # b) status filter — NEEDS_SYNC → 2 rows, descending
    # ------------------------------------------------------------------
    def test_status_filter(self):
        """status='NEEDS_SYNC' must return exactly 2 rows."""
        rows = list_sync_records(db_path=TEST_DB_PATH, status="NEEDS_SYNC")

        self.assertEqual(len(rows), 2, f"Expected 2 rows for NEEDS_SYNC, got {len(rows)}")
        for row in rows:
            self.assertEqual(row["status"], "NEEDS_SYNC")

        # Ordering: LEAD_A row (Feb 25) before LEAD_B row (Feb 22).
        self.assertGreater(rows[0]["updated_at"], rows[1]["updated_at"])

    # ------------------------------------------------------------------
    # c) lead_id filter — LEAD_A → 2 rows
    # ------------------------------------------------------------------
    def test_lead_id_filter(self):
        """lead_id=LEAD_A must return exactly 2 rows."""
        rows = list_sync_records(db_path=TEST_DB_PATH, lead_id=LEAD_A)

        self.assertEqual(len(rows), 2, f"Expected 2 rows for LEAD_A, got {len(rows)}")
        for row in rows:
            self.assertEqual(row["lead_id"], LEAD_A)

    # ------------------------------------------------------------------
    # d) status + lead_id → 1 row
    # ------------------------------------------------------------------
    def test_status_and_lead_id_filter(self):
        """Combining status=NEEDS_SYNC and lead_id=LEAD_A must return exactly 1 row."""
        rows = list_sync_records(
            db_path=TEST_DB_PATH,
            status="NEEDS_SYNC",
            lead_id=LEAD_A,
        )

        self.assertEqual(len(rows), 1, f"Expected 1 row, got {len(rows)}")
        self.assertEqual(rows[0]["lead_id"], LEAD_A)
        self.assertEqual(rows[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # e) limit applied
    # ------------------------------------------------------------------
    def test_limit_applied(self):
        """limit=2 must return at most 2 rows even when more exist."""
        rows = list_sync_records(db_path=TEST_DB_PATH, limit=2)

        self.assertEqual(len(rows), 2, f"Expected 2 rows with limit=2, got {len(rows)}")
        # Must be the two newest rows.
        self.assertEqual(rows[0]["updated_at"], "2026-02-25T12:00:00+00:00")
        self.assertEqual(rows[1]["updated_at"], "2026-02-24T12:00:00+00:00")

    # ------------------------------------------------------------------
    # f) filter that matches nothing → empty list
    # ------------------------------------------------------------------
    def test_empty_result_when_no_match(self):
        """A status that does not exist in the table must return an empty list."""
        rows = list_sync_records(db_path=TEST_DB_PATH, status="PENDING")

        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 0, "Expected empty list for non-existent status")

    # ------------------------------------------------------------------
    # g) return type — list of dict with expected keys
    # ------------------------------------------------------------------
    def test_return_type_is_list_of_dict(self):
        """Each element in the result must be a dict with the expected sync_records keys."""
        rows = list_sync_records(db_path=TEST_DB_PATH, limit=1)

        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertIsInstance(row, dict)

        expected_keys = {"id", "lead_id", "destination", "status", "created_at", "updated_at"}
        missing = expected_keys - row.keys()
        self.assertFalse(missing, f"Row dict is missing keys: {missing}")


if __name__ == "__main__":
    unittest.main()
