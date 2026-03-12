"""
tests/test_student_invite_endpoint.py

Unit tests for services/webhook/student_invite_endpoint.py.

Tests call _handle_invite_request() directly — no real HTTP server is
needed, which keeps the tests fast, deterministic, and dependency-free.
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                        # noqa: E402
from services.webhook.student_invite_endpoint import _handle_invite_request  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_invite_endpoint.db")


class TestHandleInviteRequest(unittest.TestCase):

    def setUp(self):
        """Ensure tmp/ exists and the schema is initialised before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        """Remove the isolated test database after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Test 1 — valid request returns 200 with all required keys
    # ------------------------------------------------------------------
    def test_valid_request_returns_200_with_all_keys(self):
        """A body with a valid lead_id must return HTTP 200 and a full result dict."""
        body = {
            "lead_id": "L1",
            "invite_id": "INV1",
            "base_url": "http://localhost:8501",
        }
        status, response = _handle_invite_request(body, db_path=TEST_DB_PATH)

        self.assertEqual(status, 200)
        for key in ("lead_id", "course_id", "enrollment_id", "invite_id", "token", "invite_link"):
            self.assertIn(key, response, f"Key '{key}' missing from response")

    # ------------------------------------------------------------------
    # Test 2 — valid request echoes correct values
    # ------------------------------------------------------------------
    def test_valid_request_echoes_correct_values(self):
        """Returned fields must match inputs and expected derived values."""
        body = {
            "lead_id": "L1",
            "invite_id": "INV1",
            "base_url": "http://portal.example.com",
        }
        status, response = _handle_invite_request(body, db_path=TEST_DB_PATH)

        self.assertEqual(status, 200)
        self.assertEqual(response["lead_id"], "L1")
        self.assertEqual(response["course_id"], "FREE_INTRO_AI_V0")
        self.assertEqual(response["enrollment_id"], "ENR_L1_FREE_INTRO_AI_V0")
        self.assertEqual(response["invite_id"], "INV1")
        self.assertIn(response["token"], response["invite_link"])
        self.assertTrue(response["invite_link"].startswith("http://portal.example.com"))

    # ------------------------------------------------------------------
    # Test 3 — missing lead_id returns 400
    # ------------------------------------------------------------------
    def test_missing_lead_id_returns_400(self):
        """A body without lead_id must return HTTP 400 with an error message."""
        status, response = _handle_invite_request({}, db_path=TEST_DB_PATH)

        self.assertEqual(status, 400)
        self.assertIn("error", response)
        self.assertIn("lead_id", response["error"])

    # ------------------------------------------------------------------
    # Test 4 — empty string lead_id returns 400
    # ------------------------------------------------------------------
    def test_empty_lead_id_returns_400(self):
        """A body with lead_id='' must return HTTP 400."""
        status, response = _handle_invite_request({"lead_id": ""}, db_path=TEST_DB_PATH)

        self.assertEqual(status, 400)
        self.assertIn("error", response)

    # ------------------------------------------------------------------
    # Test 5 — whitespace-only lead_id returns 400
    # ------------------------------------------------------------------
    def test_whitespace_lead_id_returns_400(self):
        """A body with lead_id='   ' must return HTTP 400."""
        status, response = _handle_invite_request({"lead_id": "   "}, db_path=TEST_DB_PATH)

        self.assertEqual(status, 400)
        self.assertIn("error", response)

    # ------------------------------------------------------------------
    # Test 6 — explicit course_id is stored and echoed
    # ------------------------------------------------------------------
    def test_explicit_course_id_stored_and_echoed(self):
        """When course_id is supplied, it must appear in the response and the DB."""
        body = {
            "lead_id": "L1",
            "course_id": "OTHER_COURSE_V1",
            "invite_id": "INV1",
        }
        status, response = _handle_invite_request(body, db_path=TEST_DB_PATH)

        self.assertEqual(status, 200)
        self.assertEqual(response["course_id"], "OTHER_COURSE_V1")
        self.assertEqual(response["enrollment_id"], "ENR_L1_OTHER_COURSE_V1")

        conn = connect(TEST_DB_PATH)
        try:
            enr = conn.execute(
                "SELECT course_id FROM course_enrollments WHERE id = ?",
                ("ENR_L1_OTHER_COURSE_V1",),
            ).fetchone()
            inv = conn.execute(
                "SELECT course_id FROM course_invites WHERE id = ?",
                ("INV1",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(enr)
        self.assertEqual(enr["course_id"], "OTHER_COURSE_V1")
        self.assertIsNotNone(inv)
        self.assertEqual(inv["course_id"], "OTHER_COURSE_V1")

    # ------------------------------------------------------------------
    # Test 7 — optional fields are forwarded to the helper
    # ------------------------------------------------------------------
    def test_optional_fields_forwarded(self):
        """name, email, and phone in the body must be stored on the lead row."""
        body = {
            "lead_id": "L1",
            "name": "Alice",
            "email": "alice@example.com",
            "phone": "555-0100",
            "invite_id": "INV1",
        }
        status, _ = _handle_invite_request(body, db_path=TEST_DB_PATH)
        self.assertEqual(status, 200)

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT name, email, phone FROM leads WHERE id = ?", ("L1",)
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["name"], "Alice")
        self.assertEqual(row["email"], "alice@example.com")
        self.assertEqual(row["phone"], "555-0100")

    # ------------------------------------------------------------------
    # Test 8 — idempotency: same invite_id returns same token
    # ------------------------------------------------------------------
    def test_idempotent_same_invite_id(self):
        """Calling twice with the same invite_id must return the same token."""
        body = {"lead_id": "L1", "invite_id": "INV1"}
        _, first = _handle_invite_request(body, db_path=TEST_DB_PATH)
        _, second = _handle_invite_request(body, db_path=TEST_DB_PATH)

        self.assertEqual(first["token"], second["token"])

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_invites WHERE id = ?", ("INV1",)
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
