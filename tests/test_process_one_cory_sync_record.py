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
    T7  — explicit dispatch_mode="dry_run" behaves identically to default
    T8  — dispatch_mode="log_sink" writes file and marks row SENT
    T9  — unknown dispatch_mode raises ValueError before touching DB
    T10 — log_sink failure marks row FAILED and returns ok=False
    T11 — stale SENT row exists: worker still succeeds, exactly one SENT row remains
    T12 — mark_sync_record_sent returns failure: worker returns ok=False
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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

    # ------------------------------------------------------------------
    # T7 — explicit dispatch_mode="dry_run" behaves identically to default
    # ------------------------------------------------------------------
    def test_explicit_dry_run_mode_behaves_as_default(self):
        self._seed("CORY_BOOKING")

        result = process_one_cory_sync_record(
            db_path=TEST_DB_PATH, now=NOW_STR, dispatch_mode="dry_run"
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["processed"])

        rows = self._rows()
        stored = json.loads(rows[0]["response_json"])
        self.assertFalse(stored["dispatched"])
        self.assertEqual(stored["mode"], "dry_run")

    # ------------------------------------------------------------------
    # T8 — dispatch_mode="log_sink" writes file and marks row SENT
    # ------------------------------------------------------------------
    def test_log_sink_mode_writes_file_and_marks_sent(self):
        self._seed("CORY_BOOKING")
        tmpdir = tempfile.mkdtemp()

        try:
            result = process_one_cory_sync_record(
                db_path=TEST_DB_PATH, now=NOW_STR,
                dispatch_mode="log_sink", log_dir=tmpdir,
            )

            # Return value
            self.assertTrue(result["ok"])
            self.assertTrue(result["processed"])
            self.assertEqual(result["destination"], "CORY_BOOKING")

            # Row is SENT
            rows = self._rows()
            self.assertEqual(rows[0]["status"], "SENT")

            # response_json stores the log_sink dispatcher result
            stored = json.loads(rows[0]["response_json"])
            self.assertTrue(stored["dispatched"])
            self.assertEqual(stored["mode"], "log_sink")
            self.assertIn("path", stored)

            # The file actually exists on disk
            self.assertTrue(os.path.isfile(stored["path"]))

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # T9 — unknown dispatch_mode raises ValueError before touching DB
    # ------------------------------------------------------------------
    def test_unknown_dispatch_mode_raises_value_error(self):
        self._seed("CORY_BOOKING")

        with self.assertRaises(ValueError) as ctx:
            process_one_cory_sync_record(
                db_path=TEST_DB_PATH, now=NOW_STR, dispatch_mode="carrier_pigeon"
            )

        self.assertIn("carrier_pigeon", str(ctx.exception))

        # Row must remain untouched
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "NEEDS_SYNC")

    # ------------------------------------------------------------------
    # T10 — log_sink failure marks row FAILED and returns ok=False
    # ------------------------------------------------------------------
    def test_log_sink_failure_marks_row_failed(self):
        self._seed("CORY_BOOKING")

        with patch(
            "execution.events.process_one_cory_sync_record.dispatch_cory_log_sink",
            side_effect=OSError("disk full"),
        ):
            result = process_one_cory_sync_record(
                db_path=TEST_DB_PATH, now=NOW_STR, dispatch_mode="log_sink"
            )

        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertIn("disk full", result["error"])

        rows = self._rows()
        self.assertEqual(rows[0]["status"], "FAILED")


    # ------------------------------------------------------------------
    # T11 — stale SENT row present: worker succeeds, only one SENT row remains
    # ------------------------------------------------------------------
    def test_stale_sent_row_does_not_block_re_dispatch(self):
        """A pre-existing SENT row must not prevent the new NEEDS_SYNC row from
        being promoted; exactly one SENT row must remain afterward."""
        # Stale SENT row left over from a previous dispatch cycle.
        self.conn.execute(
            """
            INSERT INTO sync_records
                (lead_id, destination, status, reason, created_at, updated_at)
            VALUES (?, 'CORY_BOOKING', 'SENT', 'HOT_LEAD_BOOKING', ?, ?)
            """,
            (LEAD_ID, _SEED_TS, _SEED_TS),
        )
        self.conn.commit()
        # Fresh NEEDS_SYNC row — the one the worker should promote.
        self._seed("CORY_BOOKING", created_at="2026-03-22T18:30:00+00:00")

        result = self._call()

        self.assertTrue(result["ok"],       f"Expected ok=True, got {result}")
        self.assertTrue(result["processed"], f"Expected processed=True, got {result}")
        self.assertEqual(result["destination"], "CORY_BOOKING")

        rows = self._rows()
        sent_rows = [r for r in rows if r["status"] == "SENT"]
        self.assertEqual(len(sent_rows), 1, "Exactly one SENT row must remain")
        self.assertEqual(sent_rows[0]["response_json"] is not None, True,
                         "Promoted row must have response_json set")

    # ------------------------------------------------------------------
    # T12 — mark_sync_record_sent returns failure: worker surfaces ok=False
    # ------------------------------------------------------------------
    def test_mark_sent_failure_is_surfaced_as_worker_failure(self):
        """If mark_sync_record_sent returns a non-success result the worker
        must return ok=False with an explanatory error."""
        self._seed("CORY_BOOKING")

        with patch(
            "execution.events.process_one_cory_sync_record.mark_sync_record_sent",
            return_value={"ok": False, "reason": "RECORD_NOT_FOUND"},
        ):
            result = process_one_cory_sync_record(
                db_path=TEST_DB_PATH, now=NOW_STR
            )

        self.assertFalse(result["ok"],   f"Expected ok=False, got {result}")
        self.assertIn("error",  result)
        self.assertIn("sync_record_id", result)
        # Row must remain NEEDS_SYNC — the mock prevented the update.
        rows = self._rows()
        self.assertEqual(rows[0]["status"], "NEEDS_SYNC")


if __name__ == "__main__":
    unittest.main()
