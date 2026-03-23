"""
tests/test_dispatch_cory_ghl.py

Unit tests for execution/cory/dispatch_cory_ghl.py.

Fast, deterministic, no real network calls.  urllib.request.urlopen is patched
for all tests that exercise the HTTP path.

Scenarios covered:
    T1  — ghl_api_url=None  -> safe no-op, dispatched=False, reason=NO_URL
    T2  — ghl_api_url blank -> same no-op as T1
    T3  — valid URL, mock 200 -> dispatched=True, http_status=200, ghl_contact_id echoed
    T4  — non-2xx response (mock 422) -> HTTPError raised
    T5  — network failure (URLError) -> URLError propagates
    T6  — outbound body contains ghl_contact_id, action, dispatched_at, mode
    T7  — blank ghl_contact_id -> ValueError
    T8  — action missing "type" -> ValueError
    T9  — action=None -> ValueError
    T10 — exactly one HTTP request is made per call
"""

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.cory.dispatch_cory_ghl import dispatch_cory_ghl  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
_GHL_CONTACT_ID = "GHL-CONTACT-ABC123"
_ACTION = {
    "type":    "CORY_BOOKING",
    "message": "Your session is confirmed.",
}
_NOW        = "2026-03-23T10:00:00+00:00"
_GHL_API_URL = "https://example.invalid/ghl/cory-action"


def _mock_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: resp
    resp.__exit__  = MagicMock(return_value=False)
    return resp


class TestDispatchCoryGhl(unittest.TestCase):

    # ------------------------------------------------------------------
    # T1 — ghl_api_url=None -> no-op
    # ------------------------------------------------------------------
    def test_no_url_none_returns_no_op(self):
        result = dispatch_cory_ghl(
            _GHL_CONTACT_ID, _ACTION, ghl_api_url=None, now=_NOW
        )

        self.assertFalse(result["dispatched"])
        self.assertEqual(result["mode"],   "ghl")
        self.assertEqual(result["reason"], "NO_URL")

    # ------------------------------------------------------------------
    # T2 — ghl_api_url blank -> no-op
    # ------------------------------------------------------------------
    def test_no_url_blank_returns_no_op(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                result = dispatch_cory_ghl(
                    _GHL_CONTACT_ID, _ACTION, ghl_api_url=blank, now=_NOW
                )

                self.assertFalse(result["dispatched"])
                self.assertEqual(result["reason"], "NO_URL")

    # ------------------------------------------------------------------
    # T3 — 200 response -> dispatched=True, http_status=200, contact id echoed
    # ------------------------------------------------------------------
    def test_success_200_returns_dispatched_true(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(200)):
            result = dispatch_cory_ghl(
                _GHL_CONTACT_ID, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
            )

        self.assertTrue(result["dispatched"])
        self.assertEqual(result["mode"],           "ghl")
        self.assertEqual(result["http_status"],    200)
        self.assertEqual(result["ghl_contact_id"], _GHL_CONTACT_ID)

    # ------------------------------------------------------------------
    # T4 — non-2xx (422) -> HTTPError raised
    # ------------------------------------------------------------------
    def test_http_422_raises(self):
        error = urllib.error.HTTPError(
            url=_GHL_API_URL, code=422, msg="Unprocessable Entity", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                dispatch_cory_ghl(
                    _GHL_CONTACT_ID, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
                )

        self.assertEqual(ctx.exception.code, 422)

    # ------------------------------------------------------------------
    # T5 — network failure -> URLError propagates
    # ------------------------------------------------------------------
    def test_network_failure_raises_url_error(self):
        error = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.URLError):
                dispatch_cory_ghl(
                    _GHL_CONTACT_ID, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
                )

    # ------------------------------------------------------------------
    # T6 — outbound body contains ghl_contact_id, action, dispatched_at, mode
    # ------------------------------------------------------------------
    def test_request_body_contains_expected_fields(self):
        captured: list[bytes] = []

        def fake_urlopen(req, timeout):
            captured.append(req.data)
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dispatch_cory_ghl(
                _GHL_CONTACT_ID, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
            )

        self.assertEqual(len(captured), 1, "Exactly one request must be sent")
        body = json.loads(captured[0].decode("utf-8"))

        self.assertEqual(body["ghl_contact_id"], _GHL_CONTACT_ID)
        self.assertEqual(body["action"],         _ACTION)
        self.assertEqual(body["dispatched_at"],  _NOW)
        self.assertEqual(body["mode"],           "ghl")

    # ------------------------------------------------------------------
    # T7 — blank ghl_contact_id -> ValueError
    # ------------------------------------------------------------------
    def test_blank_ghl_contact_id_raises(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                with self.assertRaises(ValueError):
                    dispatch_cory_ghl(
                        blank, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
                    )

    # ------------------------------------------------------------------
    # T8 — action missing "type" -> ValueError
    # ------------------------------------------------------------------
    def test_action_missing_type_raises(self):
        bad_action = {"message": "no type key here"}
        with self.assertRaises(ValueError) as ctx:
            dispatch_cory_ghl(
                _GHL_CONTACT_ID, bad_action, ghl_api_url=_GHL_API_URL, now=_NOW
            )

        self.assertIn("type", str(ctx.exception))

    # ------------------------------------------------------------------
    # T9 — action=None -> ValueError
    # ------------------------------------------------------------------
    def test_none_action_raises(self):
        with self.assertRaises(ValueError):
            dispatch_cory_ghl(
                _GHL_CONTACT_ID, None, ghl_api_url=_GHL_API_URL, now=_NOW
            )

    # ------------------------------------------------------------------
    # T10 — exactly one HTTP request per call
    # ------------------------------------------------------------------
    def test_exactly_one_request_per_call(self):
        call_count = 0

        def fake_urlopen(req, timeout):
            nonlocal call_count
            call_count += 1
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dispatch_cory_ghl(
                _GHL_CONTACT_ID, _ACTION, ghl_api_url=_GHL_API_URL, now=_NOW
            )

        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
