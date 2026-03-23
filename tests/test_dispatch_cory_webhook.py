"""
tests/test_dispatch_cory_webhook.py

Unit tests for execution/events/dispatch_cory_webhook.py.

Fast, deterministic, no real network calls.  urllib.request.urlopen is patched
for all tests that exercise the HTTP path.

Scenarios covered:
    T1  — webhook_url=None -> safe no-op, dispatched=False, reason=NO_URL
    T2  — webhook_url blank string -> same no-op as T1
    T3  — valid URL, mock 200 response -> dispatched=True, http_status=200
    T4  — non-2xx response (mock 400) -> HTTPError raised
    T5  — non-2xx response (mock 500) -> HTTPError raised
    T6  — network failure (URLError) -> URLError propagates
    T7  — missing required field in row_data -> ValueError
    T8  — missing id/sync_record_id -> ValueError
    T9  — outbound request body contains all expected payload fields
    T10 — sync_record_id alias accepted (row_data uses sync_record_id instead of id)
"""

import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.events.dispatch_cory_webhook import dispatch_cory_webhook  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
_ROW = {
    "id":          42,
    "lead_id":     "WEBHOOK_TEST_LEAD",
    "destination": "CORY_BOOKING",
    "reason":      "HOT_LEAD_BOOKING",
    "created_at":  "2026-03-22T23:35:00+00:00",
}
_NOW         = "2026-03-22T23:55:00+00:00"
_WEBHOOK_URL = "https://example.invalid/cory-webhook"


def _mock_response(status: int) -> MagicMock:
    """Return a context-manager mock whose .status equals *status*."""
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: resp
    resp.__exit__  = MagicMock(return_value=False)
    return resp


class TestDispatchCoryWebhook(unittest.TestCase):

    # ------------------------------------------------------------------
    # T1 — webhook_url=None -> no-op
    # ------------------------------------------------------------------
    def test_no_url_none_returns_no_op(self):
        result = dispatch_cory_webhook(_ROW, webhook_url=None, now=_NOW)

        self.assertFalse(result["dispatched"])
        self.assertEqual(result["mode"],   "webhook")
        self.assertEqual(result["reason"], "NO_URL")

    # ------------------------------------------------------------------
    # T2 — webhook_url blank -> no-op
    # ------------------------------------------------------------------
    def test_no_url_blank_returns_no_op(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                result = dispatch_cory_webhook(_ROW, webhook_url=blank, now=_NOW)

                self.assertFalse(result["dispatched"])
                self.assertEqual(result["reason"], "NO_URL")

    # ------------------------------------------------------------------
    # T3 — 200 response -> dispatched=True, http_status=200
    # ------------------------------------------------------------------
    def test_success_200_returns_dispatched_true(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(200)):
            result = dispatch_cory_webhook(_ROW, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertTrue(result["dispatched"])
        self.assertEqual(result["mode"],        "webhook")
        self.assertEqual(result["http_status"], 200)

    # ------------------------------------------------------------------
    # T4 — 400 response -> HTTPError raised
    # ------------------------------------------------------------------
    def test_http_400_raises(self):
        error = urllib.error.HTTPError(
            url=_WEBHOOK_URL, code=400, msg="Bad Request", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                dispatch_cory_webhook(_ROW, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertEqual(ctx.exception.code, 400)

    # ------------------------------------------------------------------
    # T5 — 500 response -> HTTPError raised
    # ------------------------------------------------------------------
    def test_http_500_raises(self):
        error = urllib.error.HTTPError(
            url=_WEBHOOK_URL, code=500, msg="Internal Server Error", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                dispatch_cory_webhook(_ROW, webhook_url=_WEBHOOK_URL, now=_NOW)

    # ------------------------------------------------------------------
    # T6 — network failure -> URLError propagates
    # ------------------------------------------------------------------
    def test_network_failure_raises_url_error(self):
        error = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.URLError):
                dispatch_cory_webhook(_ROW, webhook_url=_WEBHOOK_URL, now=_NOW)

    # ------------------------------------------------------------------
    # T7 — missing required field -> ValueError
    # ------------------------------------------------------------------
    def test_missing_required_field_raises_value_error(self):
        bad_row = {k: v for k, v in _ROW.items() if k != "reason"}

        with self.assertRaises(ValueError) as ctx:
            dispatch_cory_webhook(bad_row, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertIn("reason", str(ctx.exception))

    # ------------------------------------------------------------------
    # T8 — missing id and sync_record_id -> ValueError
    # ------------------------------------------------------------------
    def test_missing_id_raises_value_error(self):
        bad_row = {k: v for k, v in _ROW.items() if k != "id"}

        with self.assertRaises(ValueError) as ctx:
            dispatch_cory_webhook(bad_row, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertIn("id", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # T9 — outbound body contains all expected payload fields
    # ------------------------------------------------------------------
    def test_request_body_contains_expected_fields(self):
        captured: list[bytes] = []

        def fake_urlopen(req, timeout):
            captured.append(req.data)
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dispatch_cory_webhook(_ROW, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertEqual(len(captured), 1, "Exactly one request must be sent")
        body = json.loads(captured[0].decode("utf-8"))

        self.assertEqual(body["sync_record_id"], _ROW["id"])
        self.assertEqual(body["lead_id"],        _ROW["lead_id"])
        self.assertEqual(body["destination"],    _ROW["destination"])
        self.assertEqual(body["reason"],         _ROW["reason"])
        self.assertEqual(body["queued_at"],      _ROW["created_at"])
        self.assertEqual(body["dispatched_at"],  _NOW)
        self.assertEqual(body["mode"],           "webhook")

    # ------------------------------------------------------------------
    # T10 — sync_record_id alias in row_data is accepted
    # ------------------------------------------------------------------
    def test_sync_record_id_alias_accepted(self):
        aliased_row = {k: v for k, v in _ROW.items() if k != "id"}
        aliased_row["sync_record_id"] = _ROW["id"]

        captured: list[bytes] = []

        def fake_urlopen(req, timeout):
            captured.append(req.data)
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = dispatch_cory_webhook(aliased_row, webhook_url=_WEBHOOK_URL, now=_NOW)

        self.assertTrue(result["dispatched"])
        body = json.loads(captured[0].decode("utf-8"))
        self.assertEqual(body["sync_record_id"], _ROW["id"])


if __name__ == "__main__":
    unittest.main()
