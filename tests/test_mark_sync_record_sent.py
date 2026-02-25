"""
tests/test_mark_sync_record_sent.py

Unit tests for execution/leads/mark_sync_record_sent.py.

Fast, deterministic, no network.  `now` always injected.
Isolated SQLite file created and removed per test.

Scenarios:
    T1  — Success transition: NEEDS_SYNC → SENT, row content correct
    T2  — response_json stored on success
    T3  — Missing lead: ok=False, reason=LEAD_NOT_FOUND
    T4  — No NEEDS_SYNC row (lead exists, no sync_records): ok=False, reason=NO_NEEDS_SYNC_ROW
    T5  — Idempotency: SENT row already present, no NEEDS_SYNC → ok=True, changed=False
    T6  — Constraint safety: BOTH NEEDS_SYNC and SENT present → ok=True, changed=False,
          no IntegrityError, row counts unchanged
    T7  — After success, exactly one row remains (SENT); no residual NEEDS_SYNC row
    T8  — Non-default destination respected (row with destination="OTHER" not touched)
"""

import os
import sqlite3
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

from execution.db.sqlite import connect, init_db                      # noqa: E402
from execution.leads.mark_sync_record_sent import mark_sync_record_sent  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_mark_sync_record_sent.db")

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------
LEAD_ID = "SENT_TEST_LEAD_01"
NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
NOW_STR = NOW.isoformat()
_TS = "2026-02-24T00:00:00+00:00"   # created_at value for seeded rows


class TestMarkSyncRecordSent(unittest.TestCase):

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
        response_json: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, response_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lead_id, destination, status, response_json, _TS, _TS),
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
        return mark_sync_record_sent(
            lead_id=lead_id,
            now=NOW,
            db_path=TEST_DB_PATH,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # T1 — Success transition: NEEDS_SYNC → SENT, row content correct
    # ------------------------------------------------------------------
    def test_success_transition_needs_sync_to_sent(self):
        """NEEDS_SYNC row must be updated to SENT; timestamps and status correct."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertTrue(result["changed"], f"Expected changed=True, got {result}")
        self.assertEqual(result["status"], "SENT")

        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 1, "Exactly one row must remain after transition")
        row = rows[0]
        self.assertEqual(row["status"], "SENT")
        self.assertEqual(row["updated_at"], NOW_STR, "updated_at must match injected now")

    # ------------------------------------------------------------------
    # T2 — response_json stored on success
    # ------------------------------------------------------------------
    def test_response_json_stored_on_success(self):
        """response_json passed to mark_sync_record_sent must be persisted."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        payload = '{"ghl_contact_id": "abc123", "http_status": 200}'
        result = self._call(response_json=payload)

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])

        row = self._get_sync_rows()[0]
        self.assertEqual(row["response_json"], payload)

    # ------------------------------------------------------------------
    # T3 — Missing lead: ok=False, reason=LEAD_NOT_FOUND
    # ------------------------------------------------------------------
    def test_missing_lead_returns_lead_not_found(self):
        """A lead_id not present in leads must return ok=False, reason=LEAD_NOT_FOUND."""
        result = mark_sync_record_sent(
            lead_id="NONEXISTENT_LEAD",
            now=NOW,
            db_path=TEST_DB_PATH,
        )

        self.assertFalse(result["ok"], f"Expected ok=False, got {result}")
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")

        # No sync_records row must have been written.
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = 'NONEXISTENT_LEAD'"
        ).fetchone()[0]
        self.assertEqual(rows, 0)

    # ------------------------------------------------------------------
    # T4 — No NEEDS_SYNC row: ok=False, reason=NO_NEEDS_SYNC_ROW
    # ------------------------------------------------------------------
    def test_no_needs_sync_row_returns_error(self):
        """Lead exists but has no sync_records row → NO_NEEDS_SYNC_ROW."""
        self._seed_lead()
        # Deliberately do NOT seed any sync_records row.

        result = self._call()

        self.assertFalse(result["ok"], f"Expected ok=False, got {result}")
        self.assertEqual(result["reason"], "NO_NEEDS_SYNC_ROW")

    # ------------------------------------------------------------------
    # T5 — Idempotency: SENT row present, no NEEDS_SYNC → changed=False
    # ------------------------------------------------------------------
    def test_idempotent_when_sent_row_already_exists(self):
        """When only a SENT row exists, return ok=True, changed=False."""
        self._seed_lead()
        self._seed_sync(status="SENT")

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["changed"], f"Expected changed=False, got {result}")
        self.assertEqual(result["status"], "SENT")

        # Row count must not have changed.
        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 1, "Row count must remain 1")
        self.assertEqual(rows[0]["status"], "SENT")

    # ------------------------------------------------------------------
    # T6 — Constraint safety: BOTH NEEDS_SYNC and SENT present
    # ------------------------------------------------------------------
    def test_constraint_safety_both_rows_present(self):
        """When NEEDS_SYNC and SENT both exist, return changed=False without IntegrityError."""
        self._seed_lead()
        # UNIQUE(lead_id, destination, status) allows both since status differs.
        self._seed_sync(status="NEEDS_SYNC")
        self._seed_sync(status="SENT")

        # Sanity check: both rows exist before the call.
        self.assertEqual(len(self._get_sync_rows()), 2)

        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["changed"], f"Expected changed=False, got {result}")
        self.assertEqual(result["status"], "SENT")

        # Row counts must be unchanged — no IntegrityError raised.
        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 2, "Both rows must still exist")
        statuses = {r["status"] for r in rows}
        self.assertEqual(statuses, {"NEEDS_SYNC", "SENT"})

    # ------------------------------------------------------------------
    # T7 — After success, exactly one SENT row; no residual NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_no_residual_needs_sync_after_transition(self):
        """After a successful transition, no NEEDS_SYNC row may remain."""
        self._seed_lead()
        self._seed_sync(status="NEEDS_SYNC")

        self._call()

        rows = self._get_sync_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "SENT")

        # Confirm no NEEDS_SYNC row lurks separately.
        ns_count = self.conn.execute(
            "SELECT COUNT(*) FROM sync_records WHERE lead_id = ? AND status = 'NEEDS_SYNC'",
            (LEAD_ID,),
        ).fetchone()[0]
        self.assertEqual(ns_count, 0, "No NEEDS_SYNC row must remain after transition")

    # ------------------------------------------------------------------
    # T8 — Non-default destination: only matching destination row is touched
    # ------------------------------------------------------------------
    def test_non_default_destination_respected(self):
        """A row for destination='OTHER' must not be affected when destination='GHL'."""
        self._seed_lead()
        # Seed a NEEDS_SYNC row for GHL.
        self._seed_sync(status="NEEDS_SYNC", destination="GHL")
        # Seed a separate row for a different destination.
        self._seed_sync(status="NEEDS_SYNC", destination="OTHER")

        result = self._call(destination="GHL")

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])

        # GHL row must now be SENT.
        ghl_rows = self._get_sync_rows(destination="GHL")
        self.assertEqual(len(ghl_rows), 1)
        self.assertEqual(ghl_rows[0]["status"], "SENT")

        # OTHER row must be untouched.
        other_rows = self._get_sync_rows(destination="OTHER")
        self.assertEqual(len(other_rows), 1)
        self.assertEqual(other_rows[0]["status"], "NEEDS_SYNC")


if __name__ == "__main__":
    unittest.main()
