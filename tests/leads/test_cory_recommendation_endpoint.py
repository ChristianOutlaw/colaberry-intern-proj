"""
tests/test_cory_recommendation_endpoint.py

Unit tests for services/webhook/cory_recommendation_endpoint.py.

Tests call _handle_cory_request() directly — no real HTTP server is needed,
which keeps the tests fast, deterministic, and dependency-free.

Scenarios covered:
    T1 — valid cory_recommendation payload (write path) → 200
    T2 — wrong event name → 400
    T3 — missing 'data' key → 400
    T4 — 'data' is not a dict (malformed payload) → 400
    T5 — consumer write path result returned correctly in response
    T6 — consumer no-write path result returned correctly in response
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                              # noqa: E402
from services.webhook.cory_recommendation_endpoint import _handle_cory_request  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_cory_recommendation_endpoint.db")

LEAD_ID = "CORY_ENDPOINT_TEST_LEAD"
BUILT_AT = "2026-03-22T19:14:36.000000Z"

_VALID_ENVELOPE: dict = {
    "event": "cory_recommendation",
    "data": {
        "lead_id": LEAD_ID,
        "section": "P2_S1",
        "event_type": "HOT_LEAD_BOOKING",
        "priority": "HIGH",
        "recommended_channel": "EMAIL",
        "reason_codes": ["HOT_ENGAGED"],
        "built_at": BUILT_AT,
    },
}


def _envelope(**data_overrides) -> dict:
    """Return a copy of _VALID_ENVELOPE with selective data-field overrides."""
    env = {
        "event": _VALID_ENVELOPE["event"],
        "data": dict(_VALID_ENVELOPE["data"]),
    }
    env["data"].update(data_overrides)
    return env


class TestHandleCoryRequest(unittest.TestCase):

    def setUp(self):
        """Create an isolated DB with one test lead before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO leads (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (LEAD_ID, "Test Lead", BUILT_AT, BUILT_AT),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _call(self, body: dict) -> tuple[int, dict]:
        return _handle_cory_request(body, db_path=TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — valid cory_recommendation payload (write path) → 200
    # ------------------------------------------------------------------
    def test_valid_cory_recommendation_returns_200(self):
        status, response = self._call(_VALID_ENVELOPE)

        self.assertEqual(status, 200)
        self.assertNotIn("error", response)

    # ------------------------------------------------------------------
    # T2 — wrong event name → 400
    # ------------------------------------------------------------------
    def test_wrong_event_name_returns_400(self):
        body = dict(_VALID_ENVELOPE)
        body["event"] = "section_completed"

        status, response = self._call(body)

        self.assertEqual(status, 400)
        self.assertIn("error", response)
        self.assertIn("cory_recommendation", response["error"])

    # ------------------------------------------------------------------
    # T3 — missing 'data' key → 400
    # ------------------------------------------------------------------
    def test_missing_data_key_returns_400(self):
        body = {"event": "cory_recommendation"}  # no 'data'

        status, response = self._call(body)

        self.assertEqual(status, 400)
        self.assertIn("error", response)

    # ------------------------------------------------------------------
    # T4 — 'data' is not a dict (malformed payload) → 400
    # ------------------------------------------------------------------
    def test_non_dict_data_returns_400(self):
        body = {"event": "cory_recommendation", "data": "not-a-dict"}

        status, response = self._call(body)

        self.assertEqual(status, 400)
        self.assertIn("error", response)

    # ------------------------------------------------------------------
    # T5 — consumer write path result returned correctly in response
    # ------------------------------------------------------------------
    def test_write_path_result_returned_in_response(self):
        """HOT_LEAD_BOOKING HIGH must return ok=True, wrote=True, destination=CORY_BOOKING."""
        status, response = self._call(_envelope(
            event_type="HOT_LEAD_BOOKING", priority="HIGH"
        ))

        self.assertEqual(status, 200)
        self.assertTrue(response.get("ok"))
        self.assertTrue(response.get("wrote"))
        self.assertEqual(response.get("destination"), "CORY_BOOKING")

    # ------------------------------------------------------------------
    # T6 — consumer no-write path result returned correctly in response
    # ------------------------------------------------------------------
    def test_no_write_path_result_returned_in_response(self):
        """NO_ACTION must return 200 with ok=True and wrote=False."""
        status, response = self._call(_envelope(event_type="NO_ACTION"))

        self.assertEqual(status, 200)
        self.assertTrue(response.get("ok"))
        self.assertFalse(response.get("wrote"))
        self.assertEqual(response.get("reason"), "NO_ACTION")


if __name__ == "__main__":
    unittest.main()
