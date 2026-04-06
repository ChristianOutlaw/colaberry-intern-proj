"""
tests/test_upsert_lead.py

Unit tests for execution/leads/upsert_lead.py.
Uses an isolated database (tmp/test_upsert_lead.db) and never touches
the application database (tmp/app.db).
"""

import os
import sys
import time
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db          # noqa: E402
from execution.leads.upsert_lead import upsert_lead       # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_upsert_lead.db")


def _fetch_lead(lead_id: str) -> dict:
    """Return a lead row as a plain dict, or {} if not found."""
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


class TestUpsertLead(unittest.TestCase):

    def setUp(self):
        """Ensure tmp/ exists and the schema is initialised before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        """Remove the isolated test database after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 1 — insert creates row and sets timestamps
    # ------------------------------------------------------------------
    def test_insert_creates_row_and_sets_timestamps(self):
        """A new lead row must be created with correct fields and equal timestamps."""
        upsert_lead("L1", phone="111", email="a@b.com", name="A", db_path=TEST_DB_PATH)

        row = _fetch_lead("L1")

        self.assertEqual(row["phone"], "111")
        self.assertEqual(row["email"], "a@b.com")
        self.assertEqual(row["name"], "A")
        self.assertIsNotNone(row["created_at"], "created_at must not be None")
        self.assertIsNotNone(row["updated_at"], "updated_at must not be None")
        self.assertEqual(
            row["created_at"],
            row["updated_at"],
            "On insert, created_at and updated_at must be equal",
        )

    # ------------------------------------------------------------------
    # Test 2 — update preserves None fields and never changes created_at
    # ------------------------------------------------------------------
    def test_update_does_not_overwrite_with_none_and_preserves_created_at(self):
        """Update must only change supplied (non-None) fields and refresh updated_at."""
        # Insert initial record.
        upsert_lead("L1", phone="111", email="a@b.com", name="A", db_path=TEST_DB_PATH)
        before = _fetch_lead("L1")

        # Small sleep so updated_at will differ on the second call.
        time.sleep(0.01)

        # Update only email; leave phone and name as None.
        upsert_lead("L1", phone=None, email="new@b.com", name=None, db_path=TEST_DB_PATH)
        after = _fetch_lead("L1")

        # Fields passed as None must not be overwritten.
        self.assertEqual(after["phone"], before["phone"], "phone must not change when None is passed")
        self.assertEqual(after["name"], before["name"], "name must not change when None is passed")

        # Supplied field must be updated.
        self.assertEqual(after["email"], "new@b.com", "email must be updated to new value")

        # created_at must never change.
        self.assertEqual(after["created_at"], before["created_at"], "created_at must not be modified on update")

        # updated_at must have been refreshed.
        self.assertNotEqual(after["updated_at"], before["updated_at"], "updated_at must change on update")


    # ------------------------------------------------------------------
    # Test 3 — timestamps must reflect injected/controlled time
    # ------------------------------------------------------------------
    def test_timestamps_use_caller_controlled_time(self):
        """Stored timestamps must equal the time provided at call time, not
        a live datetime.now() captured inside the function.

        Currently upsert_lead calls the private _utc_now() helper internally.
        This test patches that helper to a fixed value, proving that stored
        timestamps derive from it — and explicitly documents that time must be
        controllable by the caller, not generated opaquely inside execution code.

        Limitation: patching a private helper is a stopgap. The proper fix
        (tracked separately) is to add a ``now`` parameter to upsert_lead so
        callers can inject time through the public API without monkeypatching.
        """
        from unittest.mock import patch

        FIXED_TIME = "2026-02-25T12:00:00+00:00"

        with patch("execution.leads.upsert_lead._utc_now", return_value=FIXED_TIME):
            upsert_lead("L_TIME", phone="555", db_path=TEST_DB_PATH)

        row = _fetch_lead("L_TIME")
        self.assertEqual(
            row["created_at"],
            FIXED_TIME,
            "created_at must equal the injected fixed time, not a live datetime.now()",
        )
        self.assertEqual(
            row["updated_at"],
            FIXED_TIME,
            "updated_at must equal the injected fixed time, not a live datetime.now()",
        )


if __name__ == "__main__":
    unittest.main()
