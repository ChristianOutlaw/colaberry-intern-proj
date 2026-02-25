"""
tests/test_sync_records_schema.py

Unit tests for the sync_records table added to execution/db/sqlite.py.
Verifies: table existence, required columns, indexes, and UNIQUE constraint.
No network or external I/O. Uses a temporary SQLite file cleaned up after each test.
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern as all other test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_sync_records.db")


class TestSyncRecordsSchema(unittest.TestCase):
    """Schema-level tests for the sync_records outbox table."""

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _table_info(self, table: str) -> dict[str, dict]:
        """Return {column_name: row_dict} for a table via PRAGMA table_info."""
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"]: dict(row) for row in rows}

    def _seed_lead(self, lead_id: str) -> None:
        """Insert a minimal lead row so FK constraints are satisfied."""
        self.conn.execute(
            "INSERT OR IGNORE INTO leads (id, created_at, updated_at) VALUES (?, ?, ?)",
            (lead_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Test 1 — table exists
    # ------------------------------------------------------------------
    def test_sync_records_table_exists(self):
        """init_db() must create the sync_records table."""
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sync_records'"
        )
        self.assertIsNotNone(cursor.fetchone(), "sync_records table was not created by init_db()")

    # ------------------------------------------------------------------
    # Test 2 — required columns exist
    # ------------------------------------------------------------------
    def test_sync_records_required_columns(self):
        """sync_records must contain all required columns."""
        columns = self._table_info("sync_records")
        required = {"id", "lead_id", "destination", "status", "created_at", "updated_at"}
        missing = required - columns.keys()
        self.assertFalse(missing, f"sync_records is missing columns: {missing}")

    # ------------------------------------------------------------------
    # Test 3 — optional columns present
    # ------------------------------------------------------------------
    def test_sync_records_optional_columns(self):
        """sync_records must also contain the optional audit columns."""
        columns = self._table_info("sync_records")
        optional = {"reason", "payload_json", "response_json", "error"}
        missing = optional - columns.keys()
        self.assertFalse(missing, f"sync_records is missing optional columns: {missing}")

    # ------------------------------------------------------------------
    # Test 4 — indexes exist
    # ------------------------------------------------------------------
    def test_sync_records_indexes_exist(self):
        """Both indexes on sync_records must be created by init_db()."""
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'sync_records'"
        )
        index_names = {row[0] for row in cursor.fetchall()}
        self.assertIn("idx_sync_records_status", index_names)
        self.assertIn("idx_sync_records_lead_id", index_names)

    # ------------------------------------------------------------------
    # Test 5 — UNIQUE constraint prevents duplicate (lead_id, destination, status)
    # ------------------------------------------------------------------
    def test_sync_records_unique_constraint(self):
        """Inserting two rows with the same (lead_id, destination, status) must raise IntegrityError."""
        self._seed_lead("lead-dup")
        common = ("lead-dup", "GHL", "NEEDS_SYNC", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            common,
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                common,
            )
            self.conn.commit()

    # ------------------------------------------------------------------
    # Test 6 — different statuses for the same lead+destination are allowed
    # ------------------------------------------------------------------
    def test_sync_records_different_statuses_allowed(self):
        """(lead_id, destination, NEEDS_SYNC) and (lead_id, destination, SENT) must coexist."""
        self._seed_lead("lead-multi")
        ts = "2026-01-01T00:00:00Z"
        self.conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("lead-multi", "GHL", "NEEDS_SYNC", ts, ts),
        )
        self.conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("lead-multi", "GHL", "SENT", ts, ts),
        )
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = 'lead-multi'"
        ).fetchone()[0]
        self.assertEqual(count, 2)

    # ------------------------------------------------------------------
    # Test 7 — ON DELETE CASCADE removes sync_records when lead is deleted
    # ------------------------------------------------------------------
    def test_sync_records_cascade_delete(self):
        """Deleting a lead must cascade-delete its sync_records rows."""
        self._seed_lead("lead-cascade")
        ts = "2026-01-01T00:00:00Z"
        self.conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("lead-cascade", "GHL", "NEEDS_SYNC", ts, ts),
        )
        self.conn.commit()

        self.conn.execute("DELETE FROM leads WHERE id = 'lead-cascade'")
        self.conn.commit()

        count = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = 'lead-cascade'"
        ).fetchone()[0]
        self.assertEqual(count, 0, "CASCADE DELETE did not remove sync_records rows")

    # ------------------------------------------------------------------
    # Test 8 — init_db() is idempotent (safe to call twice)
    # ------------------------------------------------------------------
    def test_init_db_idempotent(self):
        """Calling init_db() a second time must not raise or destroy data."""
        self._seed_lead("lead-idem")
        ts = "2026-01-01T00:00:00Z"
        self.conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("lead-idem", "GHL", "NEEDS_SYNC", ts, ts),
        )
        self.conn.commit()

        init_db(self.conn)  # second call — must not raise

        count = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = 'lead-idem'"
        ).fetchone()[0]
        self.assertEqual(count, 1, "Existing sync_records row was lost after second init_db() call")


if __name__ == "__main__":
    unittest.main()
