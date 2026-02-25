"""
tests/test_mark_sync_record_failed.py

Unit tests for execution/leads/mark_sync_record_failed.py.

Fast, deterministic, no network.  `now` always injected.
Isolated SQLite file created and removed per test.

Scenarios:
    T1  — Success transition: NEEDS_SYNC → FAILED, updated_at matches NOW, status=FAILED
    T2  — error field stored on success
    T3  — response_json stored on success (independent of error)
    T4  — Missing lead: ok=False, reason=LEAD_NOT_FOUND
    T5  — No NEEDS_SYNC row (lead exists, no sync_records): ok=False, reason=NO_NEEDS_SYNC_ROW
    T6  — Idempotency: FAILED row already present, no NEEDS_SYNC → ok=True, changed=False
    T7  — Constraint safety: BOTH NEEDS_SYNC and FAILED present → ok=True, changed=False,
          no IntegrityError, both rows unchanged
    T8  — Non-default destination respected (OTHER row untouched)
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern as all other test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                        # noqa: E402
from execution.leads.mark_sync_record_failed import mark_sync_record_failed  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_mark_sync_record_failed.db")

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------
LEAD_ID = "FAILED_TEST_LEAD_01"
NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
NOW_STR = NOW.isoformat()
_TS = "2026-02-24T00:00:00+00:00"   # created_at value for seeded rows


class TestMarkSyncRecordFailed(unittest.TestCase):

    def setUp(self):
        """Create empty isolated database before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Seed helpers
    # ------------------------------------------------------------------

    def _seed_lead(self, lead_id: str = LEAD_ID) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO leads (id, created_at, updated_at) VALUES (?, ?, ?)",
            (lead_id, _TS, _TS),
        )
        self.conn.commit()

    def _seed_sync(
        self,
        lead_id: str = LEAD_ID,
        status: str = "NEEDS_SYNC",
        destination: str = "GHL",
        error: str | None = None,
        response_json: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, error, response_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (lead_id, destination, status, error, response_json, _TS, _TS),
        )
        self.conn.commit()

    def _get_sync_rows(
        self, lead_id: str = LEAD_ID, destination: str = "GHL"
    ) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sync_records WHERE lead_id = ? AND destination = ?",
            (lead_id, destination),
        ).fetchall()
        return [dict(r) for r in rows]

    def _call(self, lead_id: str = LEAD_ID, **kwargs) -> dict:
        return mark_sync_record_failed(
            lead_id=lead_id,
            now=NOW,
            db_path=TEST_DB_PATH,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # T1 — Success transition: NEEDS_SYNC → FAILED
    # ------------------------------------------------------------------
    def test_success_transition_needs_sync_to_failed(self):
        """NEEDS_SYNC row must be updated to FAILED; timestamps and status correct."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertTrue(result["changed"], f"Expected changed=True, got {result}")
        self.assertEqual(result["status"], "FAILED")

        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 1, "Exactly one row must remain after transition")
        row = rows[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["updated_at"], NOW_STR, "updated_at must match injected now")

    # ------------------------------------------------------------------
    # T2 — error field stored on success
    # ------------------------------------------------------------------
    def test_error_field_stored_on_success(self):
        """error string passed to mark_sync_record_failed must be persisted."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        error_msg = "HTTP 500 — internal server error"
        result = self._call(error=error_msg)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])

        row = self._get_sync_rows()[0]
        self.assertEqual(row["error"], error_msg)

    # ------------------------------------------------------------------
    # T3 — response_json stored on success (independent of error)
    # ------------------------------------------------------------------
    def test_response_json_stored_on_success(self):
        """response_json passed to mark_sync_record_failed must be persisted."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        payload = '{"code": 500, "message": "GHL unavailable"}'
        result = self._call(response_json=payload)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])

        row = self._get_sync_rows()[0]
        self.assertEqual(row["response_json"], payload)

    # ------------------------------------------------------------------
    # T4 — Missing lead: ok=False, reason=LEAD_NOT_FOUND
    # ------------------------------------------------------------------
    def test_missing_lead_returns_lead_not_found(self):
        """A lead_id not present in leads must return ok=False, reason=LEAD_NOT_FOUND."""
        result = mark_sync_record_failed(
            lead_id="NONEXISTENT_LEAD",
            now=NOW,
            db_path=TEST_DB_PATH,
        )

        self.assertFalse(result["ok"], f"Expected ok=False, got {result}")
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")

        count = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = 'NONEXISTENT_LEAD'"
        ).fetchone()[0]
        self.assertEqual(count, 0, "No sync_records row must be written for missing lead")

    # ------------------------------------------------------------------
    # T5 — No NEEDS_SYNC row: ok=False, reason=NO_NEEDS_SYNC_ROW
    # ------------------------------------------------------------------
    def test_no_needs_sync_row_returns_error(self):
        """Lead exists but has no sync_records row → NO_NEEDS_SYNC_ROW."""
        self._seed_lead()
        # Deliberately do NOT seed any sync_records row.

        result = self._call()

        self.assertFalse(result["ok"], f"Expected ok=False, got {result}")
        self.assertEqual(result["reason"], "NO_NEEDS_SYNC_ROW")

    # ------------------------------------------------------------------
    # T6 — Idempotency: FAILED row already present, no NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_idempotent_when_failed_row_already_exists(self):
        """When only a FAILED row exists, return ok=True, changed=False."""
        self._seed_lead()
        self._seed_sync(status="FAILED")

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["changed"], f"Expected changed=False, got {result}")
        self.assertEqual(result["status"], "FAILED")

        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 1, "Row count must remain 1")
        self.assertEqual(rows[0]["status"], "FAILED")

    # ------------------------------------------------------------------
    # T7 — Constraint safety: BOTH NEEDS_SYNC and FAILED present
    # ------------------------------------------------------------------
    def test_constraint_safety_both_rows_present(self):
        """When NEEDS_SYNC and FAILED both exist, return changed=False without IntegrityError."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")
        self._seed_sync(status="FAILED")

        # Sanity: both rows exist before the call.
        self.assertEqual(len(self._get_sync_rows()), 2)

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["changed"], f"Expected changed=False, got {result}")
        self.assertEqual(result["status"], "FAILED")

        # Row counts must be unchanged — no IntegrityError raised.
        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 2, "Both rows must still exist")
        statuses = {r["status"] for r in rows}
        self.assertEqual(statuses, {"NEEDS_SYNC", "FAILED"})

    # ------------------------------------------------------------------
    # T8 — Non-default destination respected
    # ------------------------------------------------------------------
    def test_non_default_destination_respected(self):
        """A row for destination='OTHER' must not be affected when destination='GHL'."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC", destination="GHL")
        self._seed_sync(status="NEEDS_SYNC", destination="OTHER")

        result = self._call(destination="GHL")

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])

        # GHL row must now be FAILED.
        ghl_rows = self._get_sync_rows(destination="GHL")
        self.assertEqual(len(ghl_rows), 1)
        self.assertEqual(ghl_rows[0]["status"], "FAILED")

        # OTHER row must be untouched.
        other_rows = self._get_sync_rows(destination="OTHER")
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows[0]["status"], "NEEDS_SYNC")


if __name__ == "__main__":
    unittest.main()
