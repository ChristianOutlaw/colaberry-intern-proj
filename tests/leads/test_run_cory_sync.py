"""
tests/test_run_cory_sync.py

Unit tests for services/worker/run_cory_sync.py.

Tests call run() directly — no subprocess, no shell invocation needed because
the runner contains no argument-parsing logic beyond env-var reads that happen
only under __main__.  Testing run() is sufficient and keeps tests fast and
deterministic.

Scenarios covered:
    T1  — no pending Cory rows  -> NO_PENDING result, printed JSON matches
    T2  — one CORY_BOOKING row  -> processed=True, one row marked SENT
    T3  — run() prints valid JSON to stdout
    T4  — only one row processed per call when multiple CORY rows exist
    T5  — default mode (dry_run) still works after signature change
    T6  — log_sink mode writes a real file and marks row SENT
    T7  — invalid dispatch_mode propagates as ValueError
    T8  — webhook mode, no URL -> safe no-op JSON output, row stays NEEDS_SYNC
    T9  — webhook mode, mocked 200 -> processed=True, row SENT
    T10 — webhook mode prints valid JSON for no-op path
    T11 — ghl mode, no URL -> safe no-op, row stays NEEDS_SYNC
    T12 — ghl mode, mocked 200 -> processed=True, row SENT
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure services/worker is importable.
WORKER_DIR = str(REPO_ROOT / "services" / "worker")
if WORKER_DIR not in sys.path:
    sys.path.insert(0, WORKER_DIR)

from execution.db.sqlite import connect, init_db                # noqa: E402
from services.worker.run_cory_sync import run                   # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_cory_sync.db")

LEAD_ID  = "RUN_CORY_SYNC_TEST_LEAD"
NOW_STR  = datetime(2026, 3, 22, 20, 0, 0, tzinfo=timezone.utc).isoformat()
_SEED_TS = "2026-03-22T19:00:00+00:00"


class TestRunCorySync(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        # Remove stale db from any previous failed run before creating fresh one.
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        try:
            init_db(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO leads
                    (id, name, ghl_contact_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (LEAD_ID, "Runner Test Lead", "GHL-RUNNER-TEST-CID", _SEED_TS, _SEED_TS),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _seed(self, destination: str, created_at: str = _SEED_TS) -> None:
        conn = connect(TEST_DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO sync_records
                    (lead_id, destination, status, reason, created_at, updated_at)
                VALUES (?, ?, 'NEEDS_SYNC', ?, ?, ?)
                """,
                (LEAD_ID, destination, destination.replace("CORY_", ""), created_at, created_at),
            )
            conn.commit()
        finally:
            conn.close()

    def _rows(self) -> list[dict]:
        conn = connect(TEST_DB_PATH)
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM sync_records WHERE lead_id = ?", (LEAD_ID,)
            ).fetchall()]
        finally:
            conn.close()
        return rows

    def _call(self, dispatch_mode: str = "dry_run", log_dir: str | None = None,
              webhook_url: str | None = None, ghl_api_url: str | None = None) -> dict:
        return run(db_path=TEST_DB_PATH, now=NOW_STR,
                   dispatch_mode=dispatch_mode, log_dir=log_dir,
                   webhook_url=webhook_url, ghl_api_url=ghl_api_url)

    # ------------------------------------------------------------------
    # T1 — no pending rows -> NO_PENDING
    # ------------------------------------------------------------------
    def test_no_pending_returns_no_pending(self):
        result = self._call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "NO_PENDING")

    # ------------------------------------------------------------------
    # T2 — one CORY_BOOKING row -> processed, row marked SENT
    # ------------------------------------------------------------------
    def test_cory_booking_row_is_processed(self):
        self._seed("CORY_BOOKING")

        result = self._call()

        self.assertTrue(result["ok"])
        self.assertTrue(result["processed"])
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._rows()[0]["status"], "SENT")

    # ------------------------------------------------------------------
    # T3 — run() prints valid JSON to stdout
    # ------------------------------------------------------------------
    def test_output_is_valid_json(self):
        self._seed("CORY_BOOKING")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            run(db_path=TEST_DB_PATH, now=NOW_STR)
            printed = mock_stdout.getvalue().strip()

        parsed = json.loads(printed)   # raises if not valid JSON
        self.assertIn("ok", parsed)
        self.assertIn("processed", parsed)

    # ------------------------------------------------------------------
    # T4 — only one row processed per call (oldest first)
    # ------------------------------------------------------------------
    def test_only_one_row_processed_per_call(self):
        self._seed("CORY_NUDGE",   created_at="2026-03-22T10:00:00+00:00")
        self._seed("CORY_BOOKING", created_at="2026-03-22T11:00:00+00:00")

        result = self._call()

        self.assertTrue(result["processed"])
        self.assertEqual(result["destination"], "CORY_NUDGE")

        rows = self._rows()
        statuses = {r["destination"]: r["status"] for r in rows}
        self.assertEqual(statuses["CORY_NUDGE"],   "SENT")
        self.assertEqual(statuses["CORY_BOOKING"], "NEEDS_SYNC")


    # ------------------------------------------------------------------
    # T5 — default mode (dry_run) still works after signature change
    # ------------------------------------------------------------------
    def test_default_dry_run_mode_unchanged(self):
        self._seed("CORY_BOOKING")

        result = self._call()  # dispatch_mode defaults to "dry_run"

        self.assertTrue(result["ok"])
        self.assertTrue(result["processed"])
        self.assertEqual(self._rows()[0]["status"], "SENT")

        import json as _json
        stored = _json.loads(self._rows()[0]["response_json"])
        self.assertFalse(stored["dispatched"])
        self.assertEqual(stored["mode"], "dry_run")

    # ------------------------------------------------------------------
    # T6 — log_sink mode writes a real file and marks row SENT
    # ------------------------------------------------------------------
    def test_log_sink_mode_writes_file_and_marks_sent(self):
        self._seed("CORY_BOOKING")
        tmpdir = tempfile.mkdtemp()

        try:
            result = self._call(dispatch_mode="log_sink", log_dir=tmpdir)

            self.assertTrue(result["ok"])
            self.assertTrue(result["processed"])
            self.assertEqual(self._rows()[0]["status"], "SENT")

            stored = json.loads(self._rows()[0]["response_json"])
            self.assertTrue(stored["dispatched"])
            self.assertEqual(stored["mode"], "log_sink")
            self.assertTrue(os.path.isfile(stored["path"]))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # T7 — invalid dispatch_mode propagates as ValueError
    # ------------------------------------------------------------------
    def test_invalid_dispatch_mode_raises_value_error(self):
        self._seed("CORY_BOOKING")

        with self.assertRaises(ValueError):
            self._call(dispatch_mode="fax_machine")

        # Row must remain untouched
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")


    # ------------------------------------------------------------------
    # T8 — webhook mode, no URL -> safe no-op, row stays NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_webhook_no_url_is_safe_no_op(self):
        """webhook mode with no URL must return ok=True, processed=False, reason=NO_URL."""
        self._seed("CORY_BOOKING")

        result = self._call(dispatch_mode="webhook", webhook_url=None)

        self.assertTrue(result["ok"],         f"Expected ok=True, got {result}")
        self.assertFalse(result["processed"],  f"Expected processed=False, got {result}")
        self.assertEqual(result["reason"], "NO_URL")
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T9 — webhook mode, mocked 200 -> processed=True, row SENT
    # ------------------------------------------------------------------
    def test_webhook_success_processes_row(self):
        """webhook mode with a mocked 200 response must mark row SENT and return processed=True."""
        self._seed("CORY_BOOKING")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = self._call(
                dispatch_mode="webhook",
                webhook_url="https://example.invalid/cory",
            )

        self.assertTrue(result["ok"],        f"Expected ok=True, got {result}")
        self.assertTrue(result["processed"],  f"Expected processed=True, got {result}")
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._rows()[0]["status"], "SENT")

    # ------------------------------------------------------------------
    # T10 — webhook no-op path prints valid JSON to stdout
    # ------------------------------------------------------------------
    def test_webhook_no_op_prints_valid_json(self):
        """webhook mode with no URL must still print valid JSON to stdout."""
        self._seed("CORY_BOOKING")

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            run(db_path=TEST_DB_PATH, now=NOW_STR,
                dispatch_mode="webhook", webhook_url=None)
            printed = mock_stdout.getvalue().strip()

        parsed = json.loads(printed)
        self.assertIn("ok", parsed)
        self.assertIn("processed", parsed)
        self.assertEqual(parsed["reason"], "NO_URL")


    # ------------------------------------------------------------------
    # T11 — ghl mode, no URL -> safe no-op, row stays NEEDS_SYNC
    # ------------------------------------------------------------------
    def test_ghl_no_url_is_safe_no_op(self):
        """ghl mode with no URL must return ok=True, processed=False, reason=NO_URL."""
        self._seed("CORY_BOOKING")

        result = self._call(dispatch_mode="ghl", ghl_api_url=None)

        self.assertTrue(result["ok"],         f"Expected ok=True, got {result}")
        self.assertFalse(result["processed"],  f"Expected processed=False, got {result}")
        self.assertEqual(result["reason"], "NO_URL")
        self.assertEqual(self._rows()[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T12 — ghl mode, mocked 200 -> processed=True, row SENT
    # ------------------------------------------------------------------
    def test_ghl_success_processes_row(self):
        """ghl mode with a mocked 200 response must mark row SENT and return processed=True."""
        self._seed("CORY_BOOKING")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = self._call(
                dispatch_mode="ghl",
                ghl_api_url="https://example.invalid/ghl/cory-action",
            )

        self.assertTrue(result["ok"],        f"Expected ok=True, got {result}")
        self.assertTrue(result["processed"],  f"Expected processed=True, got {result}")
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertEqual(self._rows()[0]["status"], "SENT")


if __name__ == "__main__":
    unittest.main()
