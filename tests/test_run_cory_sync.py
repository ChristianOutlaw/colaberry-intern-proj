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
"""

import io
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
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
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO leads (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (LEAD_ID, "Runner Test Lead", _SEED_TS, _SEED_TS),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _seed(self, destination: str, created_at: str = _SEED_TS) -> None:
        conn = connect(TEST_DB_PATH)
        conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, reason, created_at, updated_at)
            VALUES (?, ?, 'NEEDS_SYNC', ?, ?, ?)
            """,
            (LEAD_ID, destination, destination.replace("CORY_", ""), created_at, created_at),
        )
        conn.commit()
        conn.close()

    def _rows(self) -> list[dict]:
        conn = connect(TEST_DB_PATH)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM sync_records WHERE lead_id = ?", (LEAD_ID,)
        ).fetchall()]
        conn.close()
        return rows

    def _call(self) -> dict:
        return run(db_path=TEST_DB_PATH, now=NOW_STR)

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


if __name__ == "__main__":
    unittest.main()
