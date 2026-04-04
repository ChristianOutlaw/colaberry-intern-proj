"""
tests/test_cory_completion_webhook.py

Focused tests for the outbound cory_recommendation webhook fired at the
shared section completion checkpoint.

Validates:
  1. When COURSE_EVENT_WEBHOOK_URL is set, send_course_event posts a
     "cory_recommendation" event with the full expected payload shape.
  2. When COURSE_EVENT_WEBHOOK_URL is unset (None), the call is a no-op —
     no network request is made.

These tests exercise send_course_event() directly with the exact payload
structure assembled by the Course Player hook, without needing to run
Streamlit.  All HTTP calls are mocked — no real network activity occurs.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.events.send_course_event import send_course_event  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixture — mirrors the payload the Course Player assembles from
# a get_cora_recommendation() result at the shared completion checkpoint.
# ---------------------------------------------------------------------------
_SAMPLE_PAYLOAD = {
    "lead_id": "demo_lead_001",
    "section": "P2_S1",
    "event_type": "NUDGE_PROGRESS",
    "priority": "MEDIUM",
    "recommended_channel": "EMAIL",
    "reason_codes": ["ACTIVE_LEARNER"],
    "built_at": "2026-03-22T19:14:36.000000Z",
}

_WEBHOOK_URL = "http://localhost:9000/events"


def _mock_response(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestCoryCompletionWebhookFires(unittest.TestCase):
    """When COURSE_EVENT_WEBHOOK_URL is set, the event is POSTed."""

    def test_event_name_is_cory_recommendation(self):
        captured: list = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data))
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = send_course_event(
                "cory_recommendation",
                _SAMPLE_PAYLOAD,
                webhook_url=_WEBHOOK_URL,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["event"], "cory_recommendation")

    def test_payload_fields_are_forwarded_intact(self):
        captured: list = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data))
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_course_event(
                "cory_recommendation",
                _SAMPLE_PAYLOAD,
                webhook_url=_WEBHOOK_URL,
            )

        data = captured[0]["data"]
        self.assertEqual(data["lead_id"], "demo_lead_001")
        self.assertEqual(data["section"], "P2_S1")
        self.assertEqual(data["event_type"], "NUDGE_PROGRESS")
        self.assertEqual(data["priority"], "MEDIUM")
        self.assertEqual(data["recommended_channel"], "EMAIL")
        self.assertEqual(data["reason_codes"], ["ACTIVE_LEARNER"])
        self.assertIn("built_at", data)

    def test_all_six_required_payload_keys_present(self):
        captured: list = []

        def fake_urlopen(req, timeout):
            captured.append(json.loads(req.data))
            return _mock_response(200)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            send_course_event(
                "cory_recommendation",
                _SAMPLE_PAYLOAD,
                webhook_url=_WEBHOOK_URL,
            )

        data = captured[0]["data"]
        required_keys = {
            "lead_id", "section", "event_type",
            "priority", "recommended_channel", "reason_codes", "built_at",
        }
        self.assertEqual(required_keys, set(data.keys()))


class TestCoryCompletionWebhookNoOp(unittest.TestCase):
    """When COURSE_EVENT_WEBHOOK_URL is unset (None), no network call is made."""

    def test_no_op_when_webhook_url_is_none(self):
        with patch("urllib.request.urlopen") as mock_open:
            result = send_course_event(
                "cory_recommendation",
                _SAMPLE_PAYLOAD,
                webhook_url=None,
            )
            mock_open.assert_not_called()

        self.assertEqual(result["status"], "no_op")
        self.assertIsNone(result["http_status"])
        self.assertIsNone(result["error"])

    def test_no_op_when_webhook_url_is_empty_string(self):
        with patch("urllib.request.urlopen") as mock_open:
            result = send_course_event(
                "cory_recommendation",
                _SAMPLE_PAYLOAD,
                webhook_url="",
            )
            mock_open.assert_not_called()

        self.assertEqual(result["status"], "no_op")

    def test_webhook_failure_does_not_raise(self):
        """A network error on the cory_recommendation webhook must never propagate."""
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            try:
                result = send_course_event(
                    "cory_recommendation",
                    _SAMPLE_PAYLOAD,
                    webhook_url=_WEBHOOK_URL,
                )
            except Exception as exc:  # noqa: BLE001
                self.fail(f"send_course_event raised unexpectedly: {exc}")

        self.assertEqual(result["status"], "error")
        self.assertIsNotNone(result["error"])


if __name__ == "__main__":
    unittest.main()
