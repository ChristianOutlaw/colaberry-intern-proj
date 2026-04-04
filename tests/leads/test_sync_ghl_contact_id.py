"""
tests/test_sync_ghl_contact_id.py

Unit tests for execution/leads/sync_ghl_contact_id.py.

Fast, deterministic, no real network calls.  urllib.request.urlopen is patched
for all tests that exercise the HTTP path.  Isolated SQLite file per test run.

Scenarios covered:
    T1  — lead not in DB -> {"ok": False, "reason": "LEAD_NOT_FOUND"}
    T2  — lead has no email or phone -> NO_LOOKUP_FIELDS, DB unchanged
    T3  — no lookup URL -> NO_LOOKUP_URL, DB unchanged
    T4  — endpoint returns no match -> NO_MATCH, DB unchanged
    T5  — endpoint returns ghl_contact_id -> DB updated, ok=True, updated=True
    T6  — existing ghl_contact_id overwritten when new match returned
    T7  — network error (URLError) propagates
    T8  — email used before phone when both are present
    T9  — phone used when email is absent
"""

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                    # noqa: E402
from execution.leads.sync_ghl_contact_id import sync_ghl_contact_id  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_sync_ghl_contact_id.db")

_LEAD_ID    = "SYNC_GHL_TEST_LEAD"
_SEED_TS    = "2026-03-23T00:00:00+00:00"
_LOOKUP_URL = "https://example.invalid/ghl/lookup"
_GHL_ID     = "ghl_abc123"


def _mock_response(ghl_contact_id: str | None) -> MagicMock:
    """Return a context-manager mock whose read() returns a JSON body."""
    body = json.dumps({"ghl_contact_id": ghl_contact_id}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: resp
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestSyncGhlContactId(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        try:
            init_db(conn)
        finally:
            conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seed(self, email: str | None = "test@example.com",
              phone: str | None = "+15550001234",
              ghl_contact_id: str | None = None) -> None:
        conn = connect(TEST_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO leads (id, email, phone, ghl_contact_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_LEAD_ID, email, phone, ghl_contact_id, _SEED_TS, _SEED_TS),
            )
            conn.commit()
        finally:
            conn.close()

    def _fetch_ghl_id(self) -> str | None:
        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT ghl_contact_id FROM leads WHERE id = ?", (_LEAD_ID,)
            ).fetchone()
        finally:
            conn.close()
        return row["ghl_contact_id"] if row else None

    def _call(self, **kwargs) -> dict:
        defaults = dict(db_path=TEST_DB_PATH, ghl_lookup_url=_LOOKUP_URL)
        defaults.update(kwargs)
        return sync_ghl_contact_id(_LEAD_ID, **defaults)

    # ------------------------------------------------------------------
    # T1 — lead not in DB -> LEAD_NOT_FOUND
    # ------------------------------------------------------------------
    def test_missing_lead_returns_lead_not_found(self):
        result = sync_ghl_contact_id(
            "NONEXISTENT_LEAD", db_path=TEST_DB_PATH, ghl_lookup_url=_LOOKUP_URL
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")

    # ------------------------------------------------------------------
    # T2 — lead has no email or phone -> NO_LOOKUP_FIELDS, DB unchanged
    # ------------------------------------------------------------------
    def test_no_lookup_fields_returns_no_op(self):
        self._seed(email=None, phone=None)

        result = self._call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "NO_LOOKUP_FIELDS")
        self.assertIsNone(self._fetch_ghl_id())

    # ------------------------------------------------------------------
    # T3 — no lookup URL -> NO_LOOKUP_URL, DB unchanged
    # ------------------------------------------------------------------
    def test_no_lookup_url_returns_no_op(self):
        self._seed()

        for blank in (None, "", "   "):
            with self.subTest(ghl_lookup_url=repr(blank)):
                result = sync_ghl_contact_id(
                    _LEAD_ID, db_path=TEST_DB_PATH, ghl_lookup_url=blank
                )
                self.assertTrue(result["ok"])
                self.assertFalse(result["updated"])
                self.assertEqual(result["reason"], "NO_LOOKUP_URL")
                self.assertIsNone(self._fetch_ghl_id())

    # ------------------------------------------------------------------
    # T4 — endpoint returns no match -> NO_MATCH, DB unchanged
    # ------------------------------------------------------------------
    def test_no_match_returns_no_op(self):
        self._seed()

        with patch("urllib.request.urlopen", return_value=_mock_response(None)):
            result = self._call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "NO_MATCH")
        self.assertIsNone(self._fetch_ghl_id())

    # ------------------------------------------------------------------
    # T5 — endpoint returns ghl_contact_id -> DB updated
    # ------------------------------------------------------------------
    def test_match_updates_db(self):
        self._seed()

        with patch("urllib.request.urlopen", return_value=_mock_response(_GHL_ID)):
            result = self._call()

        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["ghl_contact_id"], _GHL_ID)
        self.assertEqual(self._fetch_ghl_id(), _GHL_ID)

    # ------------------------------------------------------------------
    # T6 — existing ghl_contact_id is overwritten by new match
    # ------------------------------------------------------------------
    def test_existing_ghl_contact_id_is_overwritten(self):
        self._seed(ghl_contact_id="old_ghl_id")
        self.assertEqual(self._fetch_ghl_id(), "old_ghl_id")

        with patch("urllib.request.urlopen", return_value=_mock_response(_GHL_ID)):
            result = self._call()

        self.assertTrue(result["updated"])
        self.assertEqual(result["ghl_contact_id"], _GHL_ID)
        self.assertEqual(self._fetch_ghl_id(), _GHL_ID)

    # ------------------------------------------------------------------
    # T7 — network error propagates
    # ------------------------------------------------------------------
    def test_network_error_propagates(self):
        self._seed()
        error = urllib.error.URLError("connection refused")

        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.URLError):
                self._call()

        # DB must remain untouched
        self.assertIsNone(self._fetch_ghl_id())

    # ------------------------------------------------------------------
    # T8 — email used before phone when both present
    # ------------------------------------------------------------------
    def test_email_preferred_over_phone(self):
        self._seed(email="user@example.com", phone="+15550001234")
        captured: list[str] = []

        def fake_urlopen(req, timeout):
            captured.append(req.full_url)
            return _mock_response(_GHL_ID)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._call()

        self.assertEqual(len(captured), 1)
        self.assertIn("email=", captured[0])
        self.assertNotIn("phone=", captured[0])

    # ------------------------------------------------------------------
    # T9 — phone used when email absent
    # ------------------------------------------------------------------
    def test_phone_used_when_email_absent(self):
        self._seed(email=None, phone="+15550009999")
        captured: list[str] = []

        def fake_urlopen(req, timeout):
            captured.append(req.full_url)
            return _mock_response(_GHL_ID)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._call()

        self.assertEqual(len(captured), 1)
        self.assertIn("phone=", captured[0])
        self.assertNotIn("email=", captured[0])


if __name__ == "__main__":
    unittest.main()
