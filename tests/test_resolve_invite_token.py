"""
tests/test_resolve_invite_token.py

Unit tests for execution/leads/resolve_invite_token.py.
Uses an isolated database (tmp/test_resolve_token.db) and never touches
the application database (tmp/app.db).
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

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.leads.upsert_lead import upsert_lead                           # noqa: E402
from execution.leads.mark_course_invite_sent import mark_course_invite_sent   # noqa: E402
from execution.leads.resolve_invite_token import resolve_invite_token         # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_resolve_token.db")


class TestResolveInviteToken(unittest.TestCase):

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
    # T1 — valid token resolves to correct invite/lead context
    # ------------------------------------------------------------------
    def test_valid_token_resolves_correctly(self):
        """A token stored during invite creation must resolve to the correct lead and invite."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            "I1", "L1",
            sent_at="2026-01-15T10:00:00+00:00",
            channel="email",
            db_path=TEST_DB_PATH,
        )

        # Retrieve the generated token from the DB.
        conn = connect(TEST_DB_PATH)
        try:
            stored_token = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()["token"]
        finally:
            conn.close()

        result = resolve_invite_token(stored_token, db_path=TEST_DB_PATH)

        self.assertIsNotNone(result, "Expected a result dict for a valid token")
        self.assertEqual(result["invite_id"], "I1")
        self.assertEqual(result["lead_id"],   "L1")
        self.assertEqual(result["sent_at"],   "2026-01-15T10:00:00+00:00")
        self.assertEqual(result["channel"],   "email")
        self.assertEqual(result["token"],     stored_token)

    # ------------------------------------------------------------------
    # T2 — unknown token returns None
    # ------------------------------------------------------------------
    def test_unknown_token_returns_none(self):
        """A token that does not exist in the database must return None."""
        result = resolve_invite_token("nonexistent-token-xyz", db_path=TEST_DB_PATH)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T3 — None token returns None without raising
    # ------------------------------------------------------------------
    def test_none_token_returns_none(self):
        """Passing None as the token must return None, not raise an exception."""
        result = resolve_invite_token(None, db_path=TEST_DB_PATH)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T4 — empty string token returns None without raising
    # ------------------------------------------------------------------
    def test_empty_token_returns_none(self):
        """An empty string token must return None, not raise an exception."""
        result = resolve_invite_token("", db_path=TEST_DB_PATH)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T5 — output shape is complete when resolved
    # ------------------------------------------------------------------
    def test_output_shape_is_complete(self):
        """Resolved result must contain all five expected keys with correct types."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            stored_token = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()["token"]
        finally:
            conn.close()

        result = resolve_invite_token(stored_token, db_path=TEST_DB_PATH)

        for key in ("invite_id", "lead_id", "sent_at", "channel", "token"):
            self.assertIn(key, result, f"Missing key: {key}")
        self.assertIsInstance(result["invite_id"], str)
        self.assertIsInstance(result["lead_id"],   str)
        self.assertIsInstance(result["token"],     str)


if __name__ == "__main__":
    unittest.main()
