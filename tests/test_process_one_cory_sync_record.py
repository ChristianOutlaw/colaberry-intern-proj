"""
tests/test_process_one_cory_sync_record.py

Unit tests for execution/events/process_one_cory_sync_record.py.

Fast, deterministic, no network.  `now` always injected.
Isolated SQLite file created and removed per test.

Scenarios covered:
    T1  — no pending Cory rows -> NO_PENDING
    T2  — one CORY_BOOKING pending row -> marked SENT, ok=True, processed=True
    T3  — ignores non-Cory NEEDS_SYNC rows (destination="GHL") -> NO_PENDING
    T4  — processes oldest Cory row first when multiple exist
    T5  — response_json stored correctly on the SENT row
    T6  — second call after processing -> NO_PENDING
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                      # noqa: E402
from execution.events.process_one_cory_sync_record import process_one_cory_sync_record  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_process_one_cory_sync_record.db")

LEAD_ID  = "CORY_WORKER_TEST_LEAD"
NOW      = datetime(2026, 3, 22, 19, 0, 0, tzinfo=timezone.utc)
NOW_STR  = NOW.isoformat()
_SEED_TS = "2026-03-22T18:00:00+00:00"


class TestProcessOneCorySyncRecord(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.conn = connect(TEST_DB_PATH)
        init_db(self.conn)
        self.conn.execute(
            "INSERT OR IGNORE INTO leads (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (LEAD_ID, "Worker Test Lead", _SEED_TS, _SEED_TS),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call(self) -> dict:
        return process_one_cory_sync_record(db_path=TEST_DB_PATH, now=NOW_STR)

    def _seed(self, destination: str, created_at: str = _SEED_TS,
              lead_id: str = LEAD_ID) -> None:
        reason = destination.replace("CORY_", "")
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, reason, created_at, updated_at)
            VALUES (?, ?, 'NEEDS_SYNC', ?, ?, ?)
            """,
            (lead_id, destination, reason, created_at, created_at),
        )
        self.conn.commit()

    def _rows(self, lead_id: str = LEAD_ID) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sync_records WHERE lead_id = ?", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # T1 — no pending Cory rows -> NO_PENDING
    # ------------------------------------------------------------------
    def test_no_pending_returns_no_pending(self):
        result = self._call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "NO_PENDING")

    # ------------------------------------------------------------------
    # T2 — one CORY_BOOKING pending row -> marked SENT
    # ------------------------------------------------------------------
    def test_cory_booking_row_is_marked_sent(self):
        self._seed("CORY_BOOKING")

        result = self._call()

        self.assertTrue(result["ok"])
        self.assertTrue(result["processed"])
        self.assertEqual(result["destination"], "CORY_BOOKING")
        self.assertIn("sync_record_id", result)

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "SENT")

    # ------------------------------------------------------------------
    # T3 — non-Cory NEEDS_SYNC rows are ignored
    # ------------------------------------------------------------------
    def test_non_cory_rows_are_ignored(self):
        # Seed a GHL row — must not be touched.
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, reason, created_at, updated_at)
            VALUES (?, 'GHL', 'NEEDS_SYNC', 'HOT_ENGAGED', ?, ?)
            """,
            (LEAD_ID, _SEED_TS, _SEED_TS),
        )
        self.conn.commit()

        result = self._call()

        self.assertTrue(result["ok"])
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "NO_PENDING")

        # GHL row must remain NEEDS_SYNC
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "NEEDS_SYNC")
        self.assertEqual(rows[0]["destination"], "GHL")

    # ------------------------------------------------------------------
    # T4 — oldest Cory row is processed first
    # ------------------------------------------------------------------
    def test_oldest_cory_row_processed_first(self):
        # CORY_NUDGE seeded earlier → must be picked first
        self._seed("CORY_NUDGE",   created_at="2026-03-22T10:00:00+00:00")
        self._seed("CORY_BOOKING", created_at="2026-03-22T11:00:00+00:00")

        result = self._call()

        self.assertTrue(result["processed"])
        self.assertEqual(result["destination"], "CORY_NUDGE")

        # Only the older row should be SENT; the newer one stays NEEDS_SYNC
        rows = sorted(self._rows(), key=lambda r: r["destination"])
        sent_row   = next(r for r in rows if r["destination"] == "CORY_NUDGE")
        pending_row = next(r for r in rows if r["destination"] == "CORY_BOOKING")
        self.assertEqual(sent_row["status"],    "SENT")
        self.assertEqual(pending_row["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T5 — response_json stored correctly on SENT row
    # ------------------------------------------------------------------
    def test_response_json_stored_on_sent_row(self):
        self._seed("CORY_BOOKING")

        self._call()

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        stored = json.loads(rows[0]["response_json"])
        self.assertFalse(stored["dispatched"])
        self.assertEqual(stored["mode"],        "dry_run")
        self.assertEqual(stored["destination"], "CORY_BOOKING")

    # ------------------------------------------------------------------
    # T6 — second call after processing -> NO_PENDING
    # ------------------------------------------------------------------
    def test_second_call_after_processing_returns_no_pending(self):
        self._seed("CORY_BOOKING")

        first  = self._call()
        second = self._call()

        self.assertTrue(first["processed"])
        self.assertFalse(second["processed"])
        self.assertEqual(second["reason"], "NO_PENDING")


if __name__ == "__main__":
    unittest.main()
