"""
tests/test_retry_failed_ghl_writeback.py

Unit tests for execution/ghl/retry_failed_ghl_writeback.py.

Fast, deterministic, no real network calls.
Uses an isolated SQLite file (tmp/test_retry_failed_ghl_writeback.db) per test.

Scenarios covered
-----------------
T1  — row not found → ok=False, no HTTP call
T2  — row exists but wrong destination → ok=False, no HTTP call
T3  — row exists but status is not FAILED (e.g. SENT) → ok=False, no HTTP call
T4  — valid FAILED row, urlopen returns 200 → ok=True, sync_records is SENT
T5  — valid FAILED row, urlopen raises URLError → ok=False, sync_records is FAILED
"""

import os
import sys
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                    # noqa: E402
from execution.ghl.retry_failed_ghl_writeback import retry_failed_ghl_writeback    # noqa: E402

TEST_DB  = str(REPO_ROOT / "tmp" / "test_retry_failed_ghl_writeback.db")
_NOW     = "2026-03-27T12:00:00+00:00"
_GHL_URL = "http://ghl.test/contacts/update"

_DESTINATION_WRITEBACK = "GHL_WRITEBACK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_lead(lead_id: str, phone: str, ghl_contact_id: str) -> None:
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO leads
                (id, phone, ghl_contact_id, created_at, updated_at)
            VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (lead_id, phone, ghl_contact_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_invite(lead_id: str, invite_id: str, token: str) -> None:
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO course_invites (id, lead_id, token) VALUES (?, ?, ?)",
            (invite_id, lead_id, token),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_sync_record(
    lead_id: str,
    destination: str,
    status: str,
) -> int:
    """Insert a sync_records row and return its id."""
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lead_id, destination, status, _NOW, _NOW),
        )
        conn.commit()
        record_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()
    return record_id


def _get_sync_record_status(record_id: int) -> str | None:
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT status FROM sync_records WHERE id = ?", (record_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["status"] if row else None


def _make_mock_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestRetryFailedGhlWriteback(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    # ------------------------------------------------------------------
    # T1 — row not found → ok=False, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t1_row_not_found(self, mock_urlopen):
        result = retry_failed_ghl_writeback(
            99999,
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertIsNone(result["app_lead_id"])
        self.assertIn("not found", result["message"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T2 — wrong destination → ok=False, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t2_wrong_destination(self, mock_urlopen):
        _seed_lead("L_RT2", "5550100002", "GHL_RT2")
        record_id = _seed_sync_record("L_RT2", "CORY_HOT", "FAILED")

        result = retry_failed_ghl_writeback(
            record_id,
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertIn("GHL_WRITEBACK", result["message"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T3 — status is not FAILED (e.g. SENT) → ok=False, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t3_status_not_failed(self, mock_urlopen):
        _seed_lead("L_RT3", "5550100003", "GHL_RT3")
        record_id = _seed_sync_record("L_RT3", _DESTINATION_WRITEBACK, "SENT")

        result = retry_failed_ghl_writeback(
            record_id,
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertIn("FAILED", result["message"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T4 — valid FAILED row, urlopen returns 200 → ok=True, row is SENT
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-rt4"})
    @patch("urllib.request.urlopen")
    def test_t4_successful_retry_transitions_to_sent(self, mock_urlopen):
        _seed_lead("L_RT4", "5550100004", "GHL_RT4")
        _seed_invite("L_RT4", "INV_RT4", "tok-rt4")
        record_id = _seed_sync_record("L_RT4", _DESTINATION_WRITEBACK, "FAILED")
        mock_urlopen.return_value = _make_mock_response(200)

        result = retry_failed_ghl_writeback(
            record_id,
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "L_RT4")
        # write_ghl_contact_fields deletes the old row and inserts a new one;
        # query by lead_id+destination to find the current outcome row.
        conn = connect(TEST_DB)
        try:
            row = conn.execute(
                "SELECT status FROM sync_records WHERE lead_id = ? AND destination = ?",
                ("L_RT4", _DESTINATION_WRITEBACK),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "SENT")

    # ------------------------------------------------------------------
    # T5 — valid FAILED row, urlopen raises URLError → ok=False, row FAILED
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-rt5"})
    @patch("urllib.request.urlopen")
    def test_t5_failed_retry_keeps_failed_status(self, mock_urlopen):
        _seed_lead("L_RT5", "5550100005", "GHL_RT5")
        _seed_invite("L_RT5", "INV_RT5", "tok-rt5")
        record_id = _seed_sync_record("L_RT5", _DESTINATION_WRITEBACK, "FAILED")
        mock_urlopen.side_effect = urllib.error.URLError(reason="Connection refused")

        result = retry_failed_ghl_writeback(
            record_id,
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["app_lead_id"], "L_RT5")
        conn = connect(TEST_DB)
        try:
            row = conn.execute(
                "SELECT status FROM sync_records WHERE lead_id = ? AND destination = ?",
                ("L_RT5", _DESTINATION_WRITEBACK),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
