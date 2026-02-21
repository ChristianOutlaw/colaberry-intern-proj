"""
tests/test_sqlite_db.py

Unit tests for execution/db/sqlite.py.
Uses stdlib unittest only. Runs against a dedicated tmp/test_app.db file
so it never touches the application database (tmp/app.db).
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
REPO_ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()
