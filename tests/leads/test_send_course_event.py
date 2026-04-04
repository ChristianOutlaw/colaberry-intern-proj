"""
tests/test_send_course_event.py

Unit tests for execution/events/send_course_event.py.
All HTTP calls are mocked — no real network activity occurs.
"""

import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.events.send_course_event import send_course_event, _validate  # noqa: E402


def _mock_response(status: int = 200) -> MagicMock:
    """Return a mock HTTP response object usable as a context manager."""
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestSendCourseEventNoOp(unittest.TestCase):
    """Return no_op when webhook_url is absent or blank — no network call made."""

    def test_no_op_when_webhook_url_is_none(self):
        result = send_course_event("section_completed", {"lead_id": "L1"}, webhook_url=None)
        self.assertEqual(result["status"], "no_op")
        self.assertIsNone(result["http_status"])
        self.assertIsNone(result["error"])

    def test_no_op_when_webhook_url_is_empty_string(self):
        result = send_course_event("section_completed", {"lead_id": "L1"}, webhook_url="")
        self.assertEqual(result["status"], "no_op")

    def test_no_op_when_webhook_url_is_whitespace(self):
        result = send_course_event("section_completed", {"lead_id": "L1"}, webhook_url="   ")
        self.assertEqual(result["status"], "no_op")

    def test_no_op_does_not_make_network_call(self):
        with patch("urllib.request.urlopen") as mock_open:
            send_course_event("section_completed", {}, webhook_url=None)
            mock_open.assert_not_called()


class TestSendCourseEventValidation(unittest.TestCase):
    """ValueError raised for invalid event_name, payload, or timeout."""

    def test_blank_event_name_raises(self):
        with self.assertRaises(ValueError):
            send_course_event("", {})

    def test_whitespace_event_name_raises(self):
        with self.assertRaises(ValueError):
            send_course_event("   ", {})

    def test_non_string_event_name_raises(self):
        with self.assertRaises(ValueError):
            send_course_event(None, {})  # type: ignore[arg-type]

    def test_non_dict_payload_raises(self):
        with self.assertRaises(ValueError):
            send_course_event("section_completed", "not a dict")  # type: ignore[arg-type]

    def test_non_positive_timeout_raises(self):
        with self.assertRaises(ValueError):
            send_course_event("section_completed", {}, webhook_url=None, timeout_seconds=0)

    def test_negative_timeout_raises(self):
        with self.assertRaises(ValueError):
            send_course_event("section_completed", {}, webhook_url=None, timeout_seconds=-1)

    def test_bool_timeout_raises(self):
        """bool is a subclass of int — must be rejected."""
        with self.assertRaises(ValueError):
            send_course_event("section_completed", {}, webhook_url=None, timeout_seconds=True)


class TestSendCourseEventSuccess(unittest.TestCase):
    """Successful POST returns status='success' with the HTTP response code."""

    def test_success_returns_correct_shape(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(200)):
            result = send_course_event(
                "section_completed",
                {"lead_id": "L1", "section": "P1_S1"},
                webhook_url="http://example.com/hook",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["http_status"], 200)
        self.assertIsNone(result["error"])

    def test_success_with_201_created(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(201)):
            result = send_course_event(
                "invite_sent", {},
                webhook_url="http://example.com/hook",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["http_status"], 201)

    def test_post_body_contains_event_and_data(self):
        """The outbound request body must wrap payload under 'event' and 'data'."""
        import json

        captured: list = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data))
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_course_event(
                "section_completed",
                {"lead_id": "L1"},
                webhook_url="http://example.com/hook",
            )

        self.assertEqual(len(captured), 1)
        body = captured[0]
        self.assertEqual(body["event"], "section_completed")
        self.assertEqual(body["data"], {"lead_id": "L1"})


class TestSendCourseEventFailure(unittest.TestCase):
    """Non-2xx or network errors return status='error' — never raise."""

    def test_http_error_returns_error_status(self):
        http_err = urllib.error.HTTPError(
            url="http://example.com/hook",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            result = send_course_event(
                "section_completed", {},
                webhook_url="http://example.com/hook",
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["http_status"], 503)
        self.assertIn("503", result["error"])

    def test_connection_error_returns_error_status(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
            result = send_course_event(
                "section_completed", {},
                webhook_url="http://localhost:9999/hook",
            )

        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["http_status"])
        self.assertIsNotNone(result["error"])

    def test_timeout_returns_error_status(self):
        import socket
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            result = send_course_event(
                "section_completed", {},
                webhook_url="http://example.com/hook",
            )

        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["http_status"])
        self.assertIsNotNone(result["error"])

    def test_error_does_not_raise(self):
        """Any network failure must be caught and returned, never propagated."""
        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            try:
                result = send_course_event(
                    "section_completed", {},
                    webhook_url="http://example.com/hook",
                )
            except Exception as exc:  # noqa: BLE001
                self.fail(f"send_course_event raised unexpectedly: {exc}")
            self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
