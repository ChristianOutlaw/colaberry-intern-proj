"""
tests/test_process_one_cory_sync_record.py

Focused unit tests for execution/events/process_one_cory_sync_record.py.
Covers five edge cases not already exercised by test_run_cory_sync.py:

    P4  — ghl mode, ghl_contact_id is NULL → NO_GHL_CONTACT_ID, row stays NEEDS_SYNC
    P5  — ghl mode, ghl_contact_id is ""   → NO_GHL_CONTACT_ID, row stays NEEDS_SYNC
    P6  — log_sink dispatcher raises       → row transitions to FAILED
    P7  — webhook dispatcher raises        → row transitions to FAILED
    P10 — non-CORY destination row         → NO_PENDING (CORY_ prefix filter enforced)

Uses an isolated database (tmp/test_process_one_cory_sync.db) and never
touches the application database (tmp/app.db).

All tests inject a fixed NOW_STR; datetime.now() is never called.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from execution.events.process_one_cory_sync_record import process_one_cory_sync_record

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_process_one_cory_sync.db")

LEAD_ID  = "PROC_CORY_TEST_LEAD"
NOW_STR  = "2026-03-25T12:00:00+00:00"
_SEED_TS = "2026-03-25T10:00:00+00:00"


class TestProcessOneCorySyncRecord(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO leads (id, name, ghl_contact_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (LEAD_ID, "Process Cory Test Lead", None, _SEED_TS, _SEED_TS),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _seed(self, destination: str = "CORY_BOOKING") -> None:
        conn = connect(TEST_DB_PATH)
        conn.execute(
            "INSERT INTO sync_records (lead_id, destination, status, reason, created_at, updated_at) VALUES (?, ?, 'NEEDS_SYNC', ?, ?, ?)",
            (LEAD_ID, destination, destination.replace("CORY_", ""), _SEED_TS, _SEED_TS),
        )
        conn.commit()
        conn.close()

    def _row_status(self, destination: str = "CORY_BOOKING") -> str | None:
        conn = connect(TEST_DB_PATH)
        row = conn.execute(
            "SELECT status FROM sync_records WHERE lead_id = ? AND destination = ?",
            (LEAD_ID, destination),
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _call(self, dispatch_mode: str = "dry_run", **kwargs) -> dict:
        return process_one_cory_sync_record(
            db_path=TEST_DB_PATH,
            now=NOW_STR,
            dispatch_mode=dispatch_mode,
            **kwargs,
        )

    def test_ghl_missing_contact_id_returns_error(self):
        self._seed("CORY_BOOKING")
        result = self._call(dispatch_mode="ghl", ghl_api_url="https://example.invalid/ghl")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "NO_GHL_CONTACT_ID")
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._row_status("CORY_BOOKING"), "NEEDS_SYNC")

    def test_ghl_empty_string_contact_id_returns_error(self):
        conn = connect(TEST_DB_PATH)
        conn.execute("UPDATE leads SET ghl_contact_id = ? WHERE id = ?", ("", LEAD_ID))
        conn.commit()
        conn.close()
        self._seed("CORY_BOOKING")
        result = self._call(dispatch_mode="ghl", ghl_api_url="https://example.invalid/ghl")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "NO_GHL_CONTACT_ID")
        self.assertEqual(self._row_status("CORY_BOOKING"), "NEEDS_SYNC")

    def test_log_sink_exception_marks_failed(self):
        self._seed("CORY_BOOKING")
        with patch(
            "execution.events.process_one_cory_sync_record.dispatch_cory_log_sink",
            side_effect=RuntimeError("disk full"),
        ):
            result = self._call(dispatch_mode="log_sink", log_dir="/tmp/unused")
        self.assertFalse(result["ok"])
        self.assertIn("disk full", result["error"])
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._row_status("CORY_BOOKING"), "FAILED")

    def test_webhook_exception_marks_failed(self):
        self._seed("CORY_BOOKING")
        with patch(
            "execution.events.process_one_cory_sync_record.dispatch_cory_webhook",
            side_effect=RuntimeError("connection refused"),
        ):
            result = self._call(dispatch_mode="webhook", webhook_url="https://example.invalid/cory")
        self.assertFalse(result["ok"])
        self.assertIn("connection refused", result["error"])
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._row_status("CORY_BOOKING"), "FAILED")

    def test_non_cory_row_ignored_returns_no_pending(self):
        self._seed("GHL")
        result = self._call(dispatch_mode="dry_run")
        self.assertTrue(result["ok"])
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "NO_PENDING")
        self.assertEqual(self._row_status("GHL"), "NEEDS_SYNC")


if __name__ == "__main__":
    unittest.main()
