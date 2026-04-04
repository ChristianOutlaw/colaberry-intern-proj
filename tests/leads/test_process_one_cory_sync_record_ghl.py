"""
tests/test_process_one_cory_sync_record_ghl.py

Tests for the new "ghl" dispatch mode in process_one_cory_sync_record.

Fast, deterministic, no real network calls.  urllib.request.urlopen is
patched for tests that exercise the HTTP path.  Isolated SQLite file per
test class; now always injected.

Scenarios covered:
    T1  — success: ghl_contact_id present, mocked 200 -> row SENT, ok=True
    T2  — missing ghl_contact_id (blank in DB) -> ok=False, NO_GHL_CONTACT_ID,
           row stays NEEDS_SYNC
    T3  — missing ghl_contact_id (NULL in DB) -> same as T2
    T4  — ghl_api_url absent -> safe no-op, ok=True, processed=False, NO_URL,
           row stays NEEDS_SYNC
    T5  — dispatcher raises HTTPError -> row marked FAILED, ok=False
    T6  — existing modes (dry_run) unaffected by new ghl_api_url parameter
    T7  — response_json stored on SENT row contains ghl_contact_id and mode
"""

import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                         # noqa: E402
from execution.events.process_one_cory_sync_record import process_one_cory_sync_record  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_process_one_cory_sync_record_ghl.db")

_LEAD_ID     = "CORY_GHL_MODE_TEST_LEAD"
_NOW         = datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc)
_NOW_STR     = _NOW.isoformat()
_SEED_TS     = "2026-03-23T09:00:00+00:00"
_GHL_CID     = "GHL-CONTACT-XYZ"
_GHL_API_URL = "https://example.invalid/ghl/cory-action"


def _mock_urlopen(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: resp
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestProcessOneCorySyncRecordGhl(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)
        init_db(self.conn)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO leads
                (id, name, ghl_contact_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_LEAD_ID, "GHL Mode Test Lead", _GHL_CID, _SEED_TS, _SEED_TS),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seed(self, destination: str = "CORY_BOOKING") -> None:
        reason = destination.replace("CORY_", "")
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, reason, created_at, updated_at)
            VALUES (?, ?, 'NEEDS_SYNC', ?, ?, ?)
            """,
            (_LEAD_ID, destination, reason, _SEED_TS, _SEED_TS),
        )
        self.conn.commit()

    def _rows(self) -> list[dict]:
        return [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM sync_records WHERE lead_id = ?", (_LEAD_ID,)
            ).fetchall()
        ]

    def _call(self, ghl_api_url: str | None = _GHL_API_URL) -> dict:
        return process_one_cory_sync_record(
            db_path=TEST_DB_PATH,
            now=_NOW_STR,
            dispatch_mode="ghl",
            ghl_api_url=ghl_api_url,
        )

    # ------------------------------------------------------------------
    # T1 — success: ghl_contact_id present, mocked 200 -> row SENT
    # ------------------------------------------------------------------
    def test_success_marks_row_sent(self):
        self._seed()

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(200)):
            result = self._call()

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["processed"], result)
        self.assertEqual(self._rows()[0]["status"], "SENT")

    # ------------------------------------------------------------------
    # T2 — blank ghl_contact_id in DB -> NO_GHL_CONTACT_ID, row untouched
    # ------------------------------------------------------------------
    def test_blank_ghl_contact_id_returns_error(self):
        self.conn.execute(
            "UPDATE leads SET ghl_contact_id = '' WHERE id = ?", (_LEAD_ID,)
        )
        self.conn.commit()
        self._seed()

        result = self._call()

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "NO_GHL_CONTACT_ID")
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T3 — NULL ghl_contact_id in DB -> same as T2
    # ------------------------------------------------------------------
    def test_null_ghl_contact_id_returns_error(self):
        self.conn.execute(
            "UPDATE leads SET ghl_contact_id = NULL WHERE id = ?", (_LEAD_ID,)
        )
        self.conn.commit()
        self._seed()

        result = self._call()

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"], "NO_GHL_CONTACT_ID")
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T4 — ghl_api_url absent -> safe no-op, row stays NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_missing_url_is_safe_no_op(self):
        self._seed()

        result = self._call(ghl_api_url=None)

        self.assertTrue(result["ok"], result)
        self.assertFalse(result["processed"], result)
        self.assertEqual(result["reason"], "NO_URL")
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T5 — dispatcher raises HTTPError -> row FAILED, ok=False
    # ------------------------------------------------------------------
    def test_dispatcher_exception_marks_row_failed(self):
        self._seed()
        error = urllib.error.HTTPError(
            url=_GHL_API_URL, code=500, msg="Internal Server Error",
            hdrs=None, fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=error):
            result = self._call()

        self.assertFalse(result["ok"], result)
        self.assertIn("error", result)
        self.assertEqual(self._rows()[0]["status"], "FAILED")

    # ------------------------------------------------------------------
    # T6 — existing dry_run mode unaffected by new ghl_api_url parameter
    # ------------------------------------------------------------------
    def test_dry_run_mode_unaffected(self):
        self._seed()

        result = process_one_cory_sync_record(
            db_path=TEST_DB_PATH,
            now=_NOW_STR,
            dispatch_mode="dry_run",
            ghl_api_url=_GHL_API_URL,   # must be silently ignored
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["processed"], result)
        self.assertEqual(self._rows()[0]["status"], "SENT")
        stored = json.loads(self._rows()[0]["response_json"])
        self.assertEqual(stored["mode"], "dry_run")

    # ------------------------------------------------------------------
    # T7 — response_json on SENT row contains ghl_contact_id and mode
    # ------------------------------------------------------------------
    def test_response_json_stored_correctly(self):
        self._seed()

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(200)):
            self._call()

        stored = json.loads(self._rows()[0]["response_json"])
        self.assertEqual(stored["ghl_contact_id"], _GHL_CID)
        self.assertEqual(stored["mode"], "ghl")
        self.assertTrue(stored["dispatched"])


if __name__ == "__main__":
    unittest.main()
