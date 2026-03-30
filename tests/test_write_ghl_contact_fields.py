"""
tests/test_write_ghl_contact_fields.py

Unit tests for execution/ghl/write_ghl_contact_fields.py.

Fast, deterministic, no real network calls.
Uses an isolated SQLite file (tmp/test_write_ghl_contact_fields.db) per test.
All urllib.request.urlopen calls are patched via unittest.mock.

Scenarios covered
-----------------
T1  — lead not found → ok=False, sent=False, no HTTP call made
T2  — now=None raises ValueError before any DB or HTTP work
T3  — no ghl_contact_id on lead, no lookup URL → ok=False, sent=False, no HTTP
T4  — stored ghl_contact_id path → used directly, no lookup call made
T5  — successful HTTP send → ok=True, sent=True, status_code echoed
T6  — HTTP 4xx from GHL API → ok=False, sent=False, status_code in result
T7  — network URLError → ok=False, sent=False, status_code=None
T8  — no ghl_api_url configured → safe no-op, ok=True, sent=False
T9  — return shape always contains all required keys (success path)
T10 — return shape always contains all required keys (failure path)
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

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.ghl.write_ghl_contact_fields import write_ghl_contact_fields  # noqa: E402

TEST_DB  = str(REPO_ROOT / "tmp" / "test_write_ghl_contact_fields.db")
_NOW     = "2026-03-27T12:00:00+00:00"
_GHL_URL = "http://ghl.test/contacts/update"

# All keys that must be present in every return dict.
_REQUIRED_KEYS = {"ok", "app_lead_id", "ghl_contact_id", "sent", "status_code", "message"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_lead(
    lead_id: str,
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
    ghl_contact_id: str | None = None,
):
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO leads
                (id, phone, email, name, ghl_contact_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (lead_id, phone, email, name, ghl_contact_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_invite(lead_id: str, invite_id: str, token: str = "test-token") -> None:
    """Insert a minimal course_invites row so course_link can be generated."""
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO course_invites (id, lead_id, token)
            VALUES (?, ?, ?)
            """,
            (invite_id, lead_id, token),
        )
        conn.commit()
    finally:
        conn.close()


def _make_mock_response(status: int = 200) -> MagicMock:
    """Build a context-manager-compatible mock HTTP response."""
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestWriteGhlContactFields(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    # ------------------------------------------------------------------
    # T1 — lead not found → ok=False, sent=False, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t1_lead_not_found_no_http_call(self, mock_urlopen):
        result = write_ghl_contact_fields(
            "DOES_NOT_EXIST",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIsNone(result["status_code"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T2 — now=None raises ValueError before any DB or HTTP work
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t2_now_none_raises_value_error(self, mock_urlopen):
        _seed_lead("L_T2", phone="5550000001")
        with self.assertRaises(ValueError):
            write_ghl_contact_fields(
                "L_T2",
                now=None,
                ghl_api_url=_GHL_URL,
                db_path=TEST_DB,
            )
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T3 — no ghl_contact_id on lead, no lookup URL → ok=False, no HTTP
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t3_no_ghl_contact_id_no_lookup_url(self, mock_urlopen):
        _seed_lead("L_T3", phone="5550000003")  # ghl_contact_id not set

        result = write_ghl_contact_fields(
            "L_T3",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            ghl_lookup_url=None,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIsNone(result["ghl_contact_id"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T4 — stored ghl_contact_id path → used directly, no lookup call
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t4"})
    @patch("execution.leads.sync_ghl_contact_id.sync_ghl_contact_id")
    @patch("urllib.request.urlopen")
    def test_t4_stored_ghl_contact_id_skips_lookup(self, mock_urlopen, mock_sync):
        _seed_lead("L_T4", phone="5550000004", ghl_contact_id="GHL_STORED_T4")
        _seed_invite("L_T4", "INV_T4", token="tok-t4")
        mock_urlopen.return_value = _make_mock_response(200)

        result = write_ghl_contact_fields(
            "L_T4",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            ghl_lookup_url="http://lookup.test/ghl",
            db_path=TEST_DB,
        )

        # sync_ghl_contact_id must NOT have been called — stored ID is preferred
        mock_sync.assert_not_called()
        self.assertEqual(result["ghl_contact_id"], "GHL_STORED_T4")

    # ------------------------------------------------------------------
    # T5 — successful HTTP send → ok=True, sent=True, status_code echoed
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t5"})
    @patch("urllib.request.urlopen")
    def test_t5_successful_send_returns_ok_true(self, mock_urlopen):
        _seed_lead("L_T5", phone="5550000005", ghl_contact_id="GHL_T5")
        _seed_invite("L_T5", "INV_T5", token="tok-t5")
        mock_urlopen.return_value = _make_mock_response(200)

        result = write_ghl_contact_fields(
            "L_T5",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["sent"])
        self.assertEqual(result["status_code"],    200)
        self.assertEqual(result["app_lead_id"],    "L_T5")
        self.assertEqual(result["ghl_contact_id"], "GHL_T5")
        mock_urlopen.assert_called_once()

    # ------------------------------------------------------------------
    # T6 — HTTP 4xx from GHL API → ok=False, sent=False, status_code set
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t6"})
    @patch("urllib.request.urlopen")
    def test_t6_http_error_returns_ok_false_with_status_code(self, mock_urlopen):
        _seed_lead("L_T6", phone="5550000006", ghl_contact_id="GHL_T6")
        _seed_invite("L_T6", "INV_T6", token="tok-t6")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url=_GHL_URL, code=422, msg="Unprocessable Entity",
            hdrs=None, fp=BytesIO(b""),
        )

        result = write_ghl_contact_fields(
            "L_T6",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertEqual(result["status_code"],    422)
        self.assertEqual(result["ghl_contact_id"], "GHL_T6")
        self.assertIn("422", result["message"])

    # ------------------------------------------------------------------
    # T7 — network URLError → ok=False, sent=False, status_code=None
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t7"})
    @patch("urllib.request.urlopen")
    def test_t7_url_error_returns_ok_false_no_status_code(self, mock_urlopen):
        _seed_lead("L_T7", phone="5550000007", ghl_contact_id="GHL_T7")
        _seed_invite("L_T7", "INV_T7", token="tok-t7")
        mock_urlopen.side_effect = urllib.error.URLError(reason="Connection refused")

        result = write_ghl_contact_fields(
            "L_T7",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIsNone(result["status_code"])
        self.assertIn("Connection refused", result["message"])

    # ------------------------------------------------------------------
    # T8 — no ghl_api_url → safe no-op, ok=True, sent=False
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t8_no_api_url_safe_no_op(self, mock_urlopen):
        _seed_lead("L_T8", phone="5550000008", ghl_contact_id="GHL_T8")
        _seed_invite("L_T8", "INV_T8", token="tok-t8")

        result = write_ghl_contact_fields(
            "L_T8",
            now=_NOW,
            ghl_api_url=None,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIsNone(result["status_code"])
        self.assertEqual(result["ghl_contact_id"], "GHL_T8")
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T9 — return shape has all required keys on success path
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t9"})
    @patch("urllib.request.urlopen")
    def test_t9_success_return_shape(self, mock_urlopen):
        _seed_lead("L_T9", phone="5550000009", ghl_contact_id="GHL_T9")
        mock_urlopen.return_value = _make_mock_response(200)

        result = write_ghl_contact_fields(
            "L_T9",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        for key in _REQUIRED_KEYS:
            self.assertIn(key, result, f"Missing key: {key!r}")

    # ------------------------------------------------------------------
    # T10 — return shape has all required keys on failure path
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t10_failure_return_shape(self, mock_urlopen):
        # Failure: lead not found
        result = write_ghl_contact_fields(
            "NO_LEAD",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        for key in _REQUIRED_KEYS:
            self.assertIn(key, result, f"Missing key: {key!r}")
        self.assertFalse(result["ok"])
        mock_urlopen.assert_not_called()


    # ------------------------------------------------------------------
    # T12 — no invite exists → writeback blocked, ok=False, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t12_no_invite_blocks_writeback(self, mock_urlopen):
        _seed_lead("L_T12", phone="5550000012", ghl_contact_id="GHL_T12")
        # Intentionally no _seed_invite call — guard must fire.

        result = write_ghl_contact_fields(
            "L_T12",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIn("course_link not yet generated", result["message"])
        mock_urlopen.assert_not_called()

    # ------------------------------------------------------------------
    # T11 — GHL_API_KEY absent → explicit error, no HTTP call
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t11_missing_api_key_returns_explicit_error(self, mock_urlopen):
        _seed_lead("L_T11", phone="5550000011", ghl_contact_id="GHL_T11")
        _seed_invite("L_T11", "INV_T11", token="tok-t11")
        env_without_key = {k: v for k, v in os.environ.items() if k != "GHL_API_KEY"}

        with patch.dict(os.environ, env_without_key, clear=True):
            result = write_ghl_contact_fields(
                "L_T11",
                now=_NOW,
                ghl_api_url=_GHL_URL,
                db_path=TEST_DB,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertIsNone(result["status_code"])
        self.assertIn("GHL_API_KEY", result["message"])
        mock_urlopen.assert_not_called()


    # ------------------------------------------------------------------
    # T13 — failed HTTP send → sync_records row is FAILED with error set
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t13"})
    @patch("urllib.request.urlopen")
    def test_t13_failed_send_persists_failed_sync_record(self, mock_urlopen):
        _seed_lead("L_T13", phone="5550000013", ghl_contact_id="GHL_T13")
        _seed_invite("L_T13", "INV_T13", token="tok-t13")
        mock_urlopen.side_effect = urllib.error.URLError(reason="Connection refused")

        write_ghl_contact_fields(
            "L_T13",
            now=_NOW,
            ghl_api_url=_GHL_URL,
            db_path=TEST_DB,
        )

        conn = connect(TEST_DB)
        try:
            row = conn.execute(
                """
                SELECT destination, status, error
                FROM sync_records
                WHERE lead_id = ?
                """,
                ("L_T13",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Expected a sync_records row for L_T13")
        self.assertEqual(row["destination"], "GHL_WRITEBACK")
        self.assertEqual(row["status"],      "FAILED")
        self.assertIsNotNone(row["error"])
        self.assertTrue(len(row["error"]) > 0)


if __name__ == "__main__":
    unittest.main()
