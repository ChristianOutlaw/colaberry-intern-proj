"""
tests/test_process_ghl_lead_intake.py

Unit tests for execution/leads/process_ghl_lead_intake.py.

Fast, deterministic, no network calls.
Uses an isolated SQLite file (tmp/test_process_ghl_lead_intake.db) per test.

Scenarios covered
-----------------
T1  — successful flow: valid phone payload → ok=True, course_link_generated=True
T2  — matcher failure (no identity fields) → ok=False, no invite row created
T3  — idempotency: same phone payload twice → same app_lead_id and same invite token
T4  — invite not duplicated: second call does not create a second invite row
T5  — invite_generated_at present and parseable ISO-8601 string
T6  — now parameter is honoured (injected timestamp appears in response)
T7  — email fallback path produces a link
T8  — return shape contains all required keys on success
T9  — return shape contains all required keys on failure
T10 — invite_id has GHL_INTAKE_ prefix
"""

import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                         # noqa: E402
from execution.leads.process_ghl_lead_intake import process_ghl_lead_intake  # noqa: E402

TEST_DB = str(REPO_ROOT / "tmp" / "test_process_ghl_lead_intake.db")

_FIXED_NOW = "2026-03-27T12:00:00+00:00"
_BASE_URL   = "http://test.portal"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_invites(lead_id: str) -> int:
    conn = connect(TEST_DB)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM course_invites WHERE lead_id = ?", (lead_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _get_invite_token(invite_id: str) -> str | None:
    conn = connect(TEST_DB)
    try:
        row = conn.execute(
            "SELECT token FROM course_invites WHERE id = ?", (invite_id,)
        ).fetchone()
        return row["token"] if row else None
    finally:
        conn.close()


def _count_leads() -> int:
    conn = connect(TEST_DB)
    try:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestProcessGhlLeadIntake(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        conn = connect(TEST_DB)
        try:
            init_db(conn)
        finally:
            conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    # ------------------------------------------------------------------
    # T1 — successful flow: valid phone payload → link generated
    # ------------------------------------------------------------------
    def test_t1_valid_phone_payload_generates_link(self):
        result = process_ghl_lead_intake(
            {"phone": "5550001111"},
            now=_FIXED_NOW,
            base_url=_BASE_URL,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["course_link_generated"])
        self.assertIsNotNone(result["app_lead_id"])
        self.assertEqual(result["matched_by"], "created")
        # One lead and one invite row must exist.
        self.assertEqual(_count_leads(), 1)
        self.assertEqual(_count_invites(result["app_lead_id"]), 1)

    # ------------------------------------------------------------------
    # T2 — matcher failure (no identity fields) → ok=False, no invite
    # ------------------------------------------------------------------
    def test_t2_no_identity_fields_returns_ok_false_no_invite(self):
        result = process_ghl_lead_intake(
            {"ghl_contact_id": "GHL_ONLY"},   # no phone/email/name
            now=_FIXED_NOW,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["course_link_generated"])
        self.assertIsNone(result["app_lead_id"])
        # No lead and no invite created.
        self.assertEqual(_count_leads(), 0)

    # ------------------------------------------------------------------
    # T3 — idempotency: same phone payload twice → same app_lead_id + token
    # ------------------------------------------------------------------
    def test_t3_idempotency_same_phone_same_lead_same_token(self):
        payload = {"phone": "3334445555"}

        first  = process_ghl_lead_intake(payload, now=_FIXED_NOW, db_path=TEST_DB)
        second = process_ghl_lead_intake(payload, now=_FIXED_NOW, db_path=TEST_DB)

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

        # Same lead resolved both times.
        self.assertEqual(first["app_lead_id"], second["app_lead_id"])

        # Same invite token returned both times.
        token_first  = _get_invite_token(first["invite_id"])
        token_second = _get_invite_token(second["invite_id"])
        self.assertEqual(token_first, token_second)

    # ------------------------------------------------------------------
    # T4 — invite not duplicated: second call does not create a second row
    # ------------------------------------------------------------------
    def test_t4_invite_row_not_duplicated_on_second_call(self):
        payload = {"phone": "7778889999"}

        first  = process_ghl_lead_intake(payload, now=_FIXED_NOW, db_path=TEST_DB)
        _      = process_ghl_lead_intake(payload, now=_FIXED_NOW, db_path=TEST_DB)

        self.assertTrue(first["ok"])
        # Exactly one invite row must exist for this lead after two calls.
        self.assertEqual(_count_invites(first["app_lead_id"]), 1)

    # ------------------------------------------------------------------
    # T5 — invite_generated_at is present and parseable
    # ------------------------------------------------------------------
    def test_t5_invite_generated_at_is_parseable_iso8601(self):
        result = process_ghl_lead_intake(
            {"email": "ts@example.com"},
            now=_FIXED_NOW,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertIn("invite_generated_at", result)
        # Must parse without error as an ISO-8601 datetime.
        parsed = datetime.fromisoformat(result["invite_generated_at"])
        self.assertIsNotNone(parsed)

    # ------------------------------------------------------------------
    # T6 — injected now value appears in the response
    # ------------------------------------------------------------------
    def test_t6_injected_now_appears_in_response(self):
        fixed = "2026-01-15T09:30:00+00:00"
        result = process_ghl_lead_intake(
            {"phone": "1110002222"},
            now=fixed,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["invite_generated_at"], fixed)

    # ------------------------------------------------------------------
    # T7 — email fallback path also generates a link
    # ------------------------------------------------------------------
    def test_t7_email_fallback_generates_link(self):
        result = process_ghl_lead_intake(
            {"email": "email_only@example.com"},
            now=_FIXED_NOW,
            base_url=_BASE_URL,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["course_link_generated"])
        self.assertEqual(result["matched_by"], "created")

    # ------------------------------------------------------------------
    # T8 — return shape contains all required keys on success
    # ------------------------------------------------------------------
    def test_t8_success_return_shape(self):
        result = process_ghl_lead_intake(
            {"phone": "6660007777"},
            now=_FIXED_NOW,
            db_path=TEST_DB,
        )

        required_keys = {
            "ok", "app_lead_id", "matched_by",
            "course_link_generated", "invite_id",
            "invite_generated_at", "message",
            "writeback_attempted", "writeback_ok",
            "writeback_status_code", "writeback_message",
        }
        for key in required_keys:
            self.assertIn(key, result, f"Key '{key}' missing from success response")

    # ------------------------------------------------------------------
    # T9 — return shape contains all required keys on failure
    # ------------------------------------------------------------------
    def test_t9_failure_return_shape(self):
        result = process_ghl_lead_intake(
            {},   # no identity fields
            now=_FIXED_NOW,
            db_path=TEST_DB,
        )

        required_keys = {
            "ok", "app_lead_id", "matched_by", "course_link_generated", "message",
            "writeback_attempted", "writeback_ok",
            "writeback_status_code", "writeback_message",
        }
        for key in required_keys:
            self.assertIn(key, result, f"Key '{key}' missing from failure response")
        self.assertFalse(result["ok"])
        self.assertFalse(result["course_link_generated"])

    # ------------------------------------------------------------------
    # T10 — invite_id has GHL_INTAKE_ prefix
    # ------------------------------------------------------------------
    def test_t10_invite_id_has_ghl_intake_prefix(self):
        result = process_ghl_lead_intake(
            {"phone": "4445556666"},
            now=_FIXED_NOW,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(
            result["invite_id"].startswith("GHL_INTAKE_"),
            f"Expected GHL_INTAKE_ prefix, got: {result['invite_id']}",
        )


    # ------------------------------------------------------------------
    # T11 — now=None raises ValueError (determinism enforcement)
    # ------------------------------------------------------------------
    def test_t11_now_none_raises_value_error(self):
        with self.assertRaises(ValueError):
            process_ghl_lead_intake(
                {"phone": "9990001111"},
                now=None,
                db_path=TEST_DB,
            )

    # ------------------------------------------------------------------
    # T12 — full success path: writeback_ok=True when GHL returns 200
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t12"})
    @patch("urllib.request.urlopen")
    def test_t12_writeback_ok_true_on_200(self, mock_urlopen):
        # Lead has ghl_contact_id so write_ghl_contact_fields can send.
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = process_ghl_lead_intake(
            {"phone": "1112223333", "ghl_contact_id": "GHL_T12"},
            now=_FIXED_NOW,
            base_url=_BASE_URL,
            ghl_api_url="http://ghl.test/contacts/update",
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["writeback_attempted"])
        self.assertTrue(result["writeback_ok"])
        self.assertEqual(result["writeback_status_code"], 200)
        mock_urlopen.assert_called_once()

    # ------------------------------------------------------------------
    # T13 — writeback failure: GHL returns 422 → writeback_ok=False, ok still True
    # ------------------------------------------------------------------
    @patch.dict(os.environ, {"GHL_API_KEY": "test-key-t13"})
    @patch("urllib.request.urlopen")
    def test_t13_writeback_fail_does_not_hide_failure(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://ghl.test/contacts/update",
            code=422,
            msg="Unprocessable Entity",
            hdrs=None,
            fp=BytesIO(b""),
        )

        result = process_ghl_lead_intake(
            {"phone": "4445556667", "ghl_contact_id": "GHL_T13"},
            now=_FIXED_NOW,
            base_url=_BASE_URL,
            ghl_api_url="http://ghl.test/contacts/update",
            db_path=TEST_DB,
        )

        # Intake itself succeeded — lead matched and invite generated.
        self.assertTrue(result["ok"])
        self.assertTrue(result["course_link_generated"])
        # Writeback was attempted but failed — must be surfaced, not hidden.
        self.assertTrue(result["writeback_attempted"])
        self.assertFalse(result["writeback_ok"])
        self.assertEqual(result["writeback_status_code"], 422)
        self.assertIn("422", result["writeback_message"])

    # ------------------------------------------------------------------
    # T14 — matcher failure → writeback_attempted=False, writeback_ok=False
    # ------------------------------------------------------------------
    @patch("urllib.request.urlopen")
    def test_t14_matcher_failure_writeback_not_attempted(self, mock_urlopen):
        result = process_ghl_lead_intake(
            {},   # no identity fields — matcher will reject
            now=_FIXED_NOW,
            ghl_api_url="http://ghl.test/contacts/update",
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["writeback_attempted"])
        self.assertFalse(result["writeback_ok"])
        self.assertIsNone(result["writeback_status_code"])
        # No HTTP call must have been made.
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
