"""
tests/test_run_sync_ghl_contact_id.py

Unit tests for services/worker/run_sync_ghl_contact_id.py.

Tests call run() directly — no subprocess needed because the runner has no
argument-parsing logic beyond the __main__ env-var block.  This matches the
pattern established in test_run_cory_sync.py.

Scenarios covered:
    T1  — lead not found -> ok=False, reason=LEAD_NOT_FOUND
    T2  — no lookup URL  -> ok=True, updated=False, reason=NO_LOOKUP_URL
    T3  — network error  -> ok=False, error key present (exception caught)
    T4  — successful match -> ok=True, updated=True, ghl_contact_id set
    T5  — run() prints valid JSON to stdout
"""

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                         # noqa: E402
from services.worker.run_sync_ghl_contact_id import run                  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_sync_ghl_contact_id.db")

_LEAD_ID    = "RUN_SYNC_GHL_TEST_LEAD"
_SEED_TS    = "2026-03-23T00:00:00+00:00"
_LOOKUP_URL = "https://example.invalid/ghl/lookup"


def _mock_response(ghl_contact_id: str | None) -> MagicMock:
    body = json.dumps({"ghl_contact_id": ghl_contact_id}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestRunSyncGhlContactId(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        try:
            init_db(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO leads (id, name, email, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_LEAD_ID, "GHL Runner Test Lead", "runner@test.com", None, _SEED_TS, _SEED_TS),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _call(self, ghl_lookup_url: str | None = None) -> dict:
        return run(_LEAD_ID, db_path=TEST_DB_PATH, ghl_lookup_url=ghl_lookup_url)

    # ------------------------------------------------------------------
    # T1 — lead not in DB -> LEAD_NOT_FOUND
    # ------------------------------------------------------------------
    def test_lead_not_found(self):
        result = run("NONEXISTENT_LEAD", db_path=TEST_DB_PATH, ghl_lookup_url=_LOOKUP_URL)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")

    # ------------------------------------------------------------------
    # T2 — no lookup URL -> NO_LOOKUP_URL (safe no-op)
    # ------------------------------------------------------------------
    def test_no_lookup_url_is_safe_no_op(self):
        result = self._call(ghl_lookup_url=None)

        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "NO_LOOKUP_URL")

    # ------------------------------------------------------------------
    # T3 — network error is caught; ok=False, error key present
    # ------------------------------------------------------------------
    def test_network_error_is_caught(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = self._call(ghl_lookup_url=_LOOKUP_URL)

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    # ------------------------------------------------------------------
    # T4 — successful match -> updated=True, ghl_contact_id written
    # ------------------------------------------------------------------
    def test_successful_match_updates_db(self):
        with patch("urllib.request.urlopen", return_value=_mock_response("GHL-123")):
            result = self._call(ghl_lookup_url=_LOOKUP_URL)

        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["ghl_contact_id"], "GHL-123")

        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT ghl_contact_id FROM leads WHERE id = ?", (_LEAD_ID,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["ghl_contact_id"], "GHL-123")

    # ------------------------------------------------------------------
    # T5 — run() prints valid JSON to stdout
    # ------------------------------------------------------------------
    def test_output_is_valid_json(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            run(_LEAD_ID, db_path=TEST_DB_PATH, ghl_lookup_url=None)
            printed = mock_stdout.getvalue().strip()

        parsed = json.loads(printed)
        self.assertIn("ok", parsed)


if __name__ == "__main__":
    unittest.main()
