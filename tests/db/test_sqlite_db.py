"""
tests/test_sqlite_db.py

Unit tests for execution/db/sqlite.py.
Uses stdlib unittest only. Runs against a dedicated tmp/test_app.db file
so it never touches the application database (tmp/app.db).

Scenarios covered:
    T1  — connect() enables foreign keys
    T2  — init_db() creates all expected tables
    T3  — fresh init_db() creates leads with ghl_contact_id column
    T4  — init_db() is idempotent: repeated calls preserve existing data
    T5  — pre-existing leads table without ghl_contact_id is migrated correctly
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap
# Ensure the repo root is on sys.path so `execution.db.sqlite` is importable
# regardless of how the test runner is invoked.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_app.db")


class TestSqliteHelpers(unittest.TestCase):
    """Tests for the SQLite infrastructure helpers."""

    def setUp(self):
        """Open a fresh connection to the isolated test database."""
        # Ensure tmp/ exists (mirrors get_db_path behaviour)
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)

    def tearDown(self):
        """Close connection and remove the temp database file."""
        self.conn.close()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 1 — foreign key enforcement
    # ------------------------------------------------------------------
    def test_connect_enables_foreign_keys(self):
        """connect() must turn PRAGMA foreign_keys ON (returns 1)."""
        cursor = self.conn.execute("PRAGMA foreign_keys")
        value = cursor.fetchone()[0]
        self.assertEqual(value, 1, "Expected foreign_keys PRAGMA to be 1 (ON)")

    # ------------------------------------------------------------------
    # Test 2 — schema initialisation
    # ------------------------------------------------------------------
    def test_init_db_creates_all_expected_tables(self):
        """init_db() must create exactly the five required tables."""
        init_db(self.conn)

        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {
            "leads",
            "course_invites",
            "progress_events",
            "course_state",
            "hot_lead_signals",
        }

        missing = expected_tables - existing_tables
        self.assertFalse(
            missing,
            f"The following tables were not created by init_db(): {missing}",
        )


    # ------------------------------------------------------------------
    # T3 — fresh init_db() creates leads with ghl_contact_id
    # ------------------------------------------------------------------
    def test_fresh_leads_table_has_ghl_contact_id(self):
        """A freshly initialised leads table must include ghl_contact_id."""
        init_db(self.conn)

        columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        self.assertIn(
            "ghl_contact_id", columns,
            "leads table created by init_db() must have ghl_contact_id column",
        )

    # ------------------------------------------------------------------
    # T4 — init_db() is idempotent: repeated calls preserve existing data
    # ------------------------------------------------------------------
    def test_init_db_repeated_calls_preserve_leads_data(self):
        """Calling init_db() twice must not destroy existing leads rows."""
        init_db(self.conn)
        ts = "2026-01-01T00:00:00Z"
        self.conn.execute(
            "INSERT INTO leads (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("idem-lead-001", "Test Lead", ts, ts),
        )
        self.conn.commit()

        init_db(self.conn)  # second call

        row = self.conn.execute(
            "SELECT id FROM leads WHERE id = 'idem-lead-001'"
        ).fetchone()
        self.assertIsNotNone(row, "Existing leads row must survive a repeated init_db() call")

        # Column must still be present.
        columns = {
            r[1] for r in self.conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        self.assertIn("ghl_contact_id", columns)

    # ------------------------------------------------------------------
    # T5 — pre-existing leads table without ghl_contact_id is migrated
    # ------------------------------------------------------------------
    def test_existing_leads_table_migrated_to_add_ghl_contact_id(self):
        """An older leads table that lacks ghl_contact_id must have it added by init_db()."""
        # Simulate an older database: create leads WITHOUT ghl_contact_id.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id         TEXT PRIMARY KEY,
                phone      TEXT,
                email      TEXT,
                name       TEXT,
                created_at TEXT,
                updated_at TEXT
            );
        """)

        # Confirm the column is absent before migration.
        pre_columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        self.assertNotIn(
            "ghl_contact_id", pre_columns,
            "Test setup error: ghl_contact_id should not exist before init_db()",
        )

        # init_db() must add the column without touching existing rows.
        init_db(self.conn)

        post_columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        self.assertIn(
            "ghl_contact_id", post_columns,
            "init_db() must add ghl_contact_id to an existing leads table",
        )


if __name__ == "__main__":
    unittest.main()
