"""
tests/test_get_latest_invite_token.py

Unit tests for execution/leads/get_latest_invite_token.py.
Uses an isolated database (tmp/test_latest_token.db) and never touches
the application database (tmp/app.db).
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.leads.upsert_lead import upsert_lead                           # noqa: E402
from execution.leads.mark_course_invite_sent import mark_course_invite_sent   # noqa: E402
from execution.leads.get_latest_invite_token import get_latest_invite_token   # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_latest_token.db")


class TestGetLatestInviteToken(unittest.TestCase):

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
    # T1 — single invite returns its token
    # ------------------------------------------------------------------
    def test_single_invite_returns_token(self):
        """A lead with one invite must return that invite's token."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        result = get_latest_invite_token("L1", db_path=TEST_DB_PATH)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 10)

    # ------------------------------------------------------------------
    # T2 — latest token returned when multiple invites exist
    # ------------------------------------------------------------------
    def test_latest_token_returned_for_multiple_invites(self):
        """When a lead has multiple invites the most recently sent one wins."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            "I1", "L1",
            sent_at="2026-01-01T10:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        mark_course_invite_sent(
            "I2", "L1",
            sent_at="2026-03-01T10:00:00+00:00",
            db_path=TEST_DB_PATH,
        )

        # Fetch each token directly so the assertion can be exact.
        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I2",)
            ).fetchone()
            latest_token = row["token"]
        finally:
            conn.close()

        result = get_latest_invite_token("L1", db_path=TEST_DB_PATH)

        self.assertEqual(result, latest_token, "Expected the token from the most recent invite")

    # ------------------------------------------------------------------
    # T3 — unknown lead returns None
    # ------------------------------------------------------------------
    def test_unknown_lead_returns_none(self):
        """A lead ID that has no invite row must return None."""
        upsert_lead("L1", db_path=TEST_DB_PATH)  # lead exists but has no invite

        result = get_latest_invite_token("L1", db_path=TEST_DB_PATH)

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T4 — None lead_id returns None without raising
    # ------------------------------------------------------------------
    def test_none_lead_id_returns_none(self):
        """Passing None as lead_id must return None, not raise an exception."""
        result = get_latest_invite_token(None, db_path=TEST_DB_PATH)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T5 — empty string lead_id returns None without raising
    # ------------------------------------------------------------------
    def test_empty_lead_id_returns_none(self):
        """An empty string lead_id must return None, not raise an exception."""
        result = get_latest_invite_token("", db_path=TEST_DB_PATH)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # T6 — returned token matches the value stored in course_invites
    # ------------------------------------------------------------------
    def test_returned_token_matches_stored_value(self):
        """The returned token must be byte-for-byte identical to the DB value."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent("I1", "L1", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            stored = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()["token"]
        finally:
            conn.close()

        result = get_latest_invite_token("L1", db_path=TEST_DB_PATH)

        self.assertEqual(result, stored)

    # ------------------------------------------------------------------
    # T7 — works correctly when invite has an explicit course_id
    # ------------------------------------------------------------------
    def test_works_with_explicit_course_id(self):
        """get_latest_invite_token must return the correct token regardless of course_id."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        mark_course_invite_sent(
            "I1", "L1",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        conn = connect(TEST_DB_PATH)
        try:
            stored = conn.execute(
                "SELECT token FROM course_invites WHERE id = ?", ("I1",)
            ).fetchone()["token"]
        finally:
            conn.close()

        result = get_latest_invite_token("L1", db_path=TEST_DB_PATH)

        self.assertEqual(result, stored,
                         "Token must be returned correctly when invite has a non-default course_id")


if __name__ == "__main__":
    unittest.main()
