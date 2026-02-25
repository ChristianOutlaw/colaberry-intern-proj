"""
tests/test_write_hot_lead_sync_record.py

Unit tests for execution/leads/write_hot_lead_sync_record.py.

All tests are fast, deterministic, and require no network access.
`now` is always injected; datetime.now() is never called.
An isolated SQLite file is created and removed per test.

Scenarios covered:
    T1  — HOT_READY lead → writes exactly one sync_records row
          (destination="GHL", status="NEEDS_SYNC")
    T2  — Idempotency: calling twice for a HOT lead → still exactly 1 row
    T3  — INVITED_NO_PROGRESS (not hot) → writes nothing, ok=True, wrote=False
    T4  — COLD_NO_INVITE (not hot) → writes nothing, ok=True, wrote=False
    T5  — STALE_ACTIVITY (not hot) → writes nothing, ok=True, wrote=False
    T6  — Missing lead → ok=False, reason="LEAD_NOT_FOUND", 0 rows written
    T7  — HOT row content: destination, status, reason, created_at, updated_at correct
    T8  — Second call updates updated_at (or it stays same); row count stays 1
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — same pattern as all other test files in this repo.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.admin.simulate_scenario import simulate_scenario           # noqa: E402
from execution.db.sqlite import connect, init_db                          # noqa: E402
from execution.leads.write_hot_lead_sync_record import write_hot_lead_sync_record  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_write_hot_lead_sync_record.db")

# ---------------------------------------------------------------------------
# Fixture constants — deterministic; fixed across all runs.
# ---------------------------------------------------------------------------
LEAD_ID = "SYNC_OUTBOX_TEST_01"

# Injected "now": Feb 25 2026 12:00 UTC.
# HOT_READY events are recorded AT this timestamp → delta = 0 days → hot=True.
# STALE_ACTIVITY events are recorded 8 days before → delta = 8 days → hot=False.
NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


class TestWriteHotLeadSyncRecord(unittest.TestCase):

    def setUp(self):
        """Initialise the isolated test database before each test."""
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        """Remove the isolated test database after each test."""
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _setup_scenario(self, scenario_id: str, lead_id: str = LEAD_ID) -> dict:
        """Run simulate_scenario with shared test defaults."""
        return simulate_scenario(
            scenario_id=scenario_id,
            lead_id=lead_id,
            confirm=True,
            now=NOW,
            db_path=TEST_DB_PATH,
        )

    def _call(self, lead_id: str = LEAD_ID) -> dict:
        """Call write_hot_lead_sync_record with shared test defaults."""
        return write_hot_lead_sync_record(
            lead_id=lead_id,
            now=NOW,
            db_path=TEST_DB_PATH,
        )

    def _sync_rows(self, lead_id: str = LEAD_ID) -> list[dict]:
        """Return all sync_records rows for the given lead as dicts."""
        conn = connect(TEST_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT * FROM sync_records WHERE lead_id = ?", (lead_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _sync_count(self, lead_id: str = LEAD_ID) -> int:
        return len(self._sync_rows(lead_id))

    # ------------------------------------------------------------------
    # T1 — HOT_READY → exactly one sync_records row written
    # ------------------------------------------------------------------
    def test_hot_ready_writes_one_sync_record(self):
        """A HOT_READY lead must produce exactly one sync_records row."""
        self._setup_scenario("HOT_READY")
        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertTrue(result["wrote"], f"Expected wrote=True, got {result}")
        self.assertEqual(result["sync_status"], "NEEDS_SYNC")
        self.assertEqual(
            self._sync_count(), 1,
            "Exactly one sync_records row must exist after a HOT lead write",
        )

    # ------------------------------------------------------------------
    # T2 — Idempotency: calling twice produces exactly 1 row
    # ------------------------------------------------------------------
    def test_hot_ready_idempotent_second_call(self):
        """Calling write_hot_lead_sync_record twice must leave exactly 1 row."""
        self._setup_scenario("HOT_READY")

        result1 = self._call()
        result2 = self._call()

        self.assertTrue(result1["ok"])
        self.assertTrue(result2["ok"])
        self.assertEqual(
            self._sync_count(), 1,
            "Second call must not insert a duplicate row; count must stay at 1",
        )

    # ------------------------------------------------------------------
    # T3 — INVITED_NO_PROGRESS (not hot) → 0 rows, ok=True, wrote=False
    # ------------------------------------------------------------------
    def test_invited_no_progress_writes_nothing(self):
        """An invited lead with no progress is not hot; no row must be written."""
        self._setup_scenario("INVITED_NO_PROGRESS")
        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["wrote"], f"Expected wrote=False, got {result}")
        self.assertIn(
            result["reason"],
            {"COMPLETION_UNKNOWN", "COMPLETION_BELOW_THRESHOLD"},
            f"Unexpected reason: {result['reason']!r}",
        )
        self.assertEqual(self._sync_count(), 0, "No sync_records row must exist")

    # ------------------------------------------------------------------
    # T4 — COLD_NO_INVITE (not hot) → 0 rows, ok=True, wrote=False
    # ------------------------------------------------------------------
    def test_cold_no_invite_writes_nothing(self):
        """A lead with no invite fails the Invite Gate; no row must be written."""
        self._setup_scenario("COLD_NO_INVITE")
        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["wrote"], f"Expected wrote=False, got {result}")
        self.assertEqual(result["reason"], "NOT_INVITED")
        self.assertEqual(self._sync_count(), 0, "No sync_records row must exist")

    # ------------------------------------------------------------------
    # T5 — STALE_ACTIVITY (not hot) → 0 rows, ok=True, wrote=False
    # ------------------------------------------------------------------
    def test_stale_activity_writes_nothing(self):
        """A lead with stale activity fails the Recency Gate; no row must be written."""
        self._setup_scenario("STALE_ACTIVITY")
        result = self._call()

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertFalse(result["wrote"], f"Expected wrote=False, got {result}")
        self.assertEqual(result["reason"], "ACTIVITY_STALE")
        self.assertEqual(self._sync_count(), 0, "No sync_records row must exist")

    # ------------------------------------------------------------------
    # T6 — Missing lead → ok=False, reason="LEAD_NOT_FOUND", 0 rows
    # ------------------------------------------------------------------
    def test_missing_lead_returns_ok_false(self):
        """A lead that does not exist must return ok=False and write nothing."""
        result = write_hot_lead_sync_record(
            lead_id="NONEXISTENT_LEAD_XYZ",
            now=NOW,
            db_path=TEST_DB_PATH,
        )

        self.assertFalse(result["ok"], f"Expected ok=False, got {result}")
        self.assertEqual(result["reason"], "LEAD_NOT_FOUND")
        self.assertEqual(
            self._sync_count("NONEXISTENT_LEAD_XYZ"), 0,
            "No sync_records row must be written for a missing lead",
        )

    # ------------------------------------------------------------------
    # T7 — HOT row content: correct destination, status, reason, timestamps
    # ------------------------------------------------------------------
    def test_hot_ready_row_content(self):
        """The written sync_records row must have correct field values."""
        self._setup_scenario("HOT_READY")
        self._call()

        rows = self._sync_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]

        self.assertEqual(row["lead_id"], LEAD_ID)
        self.assertEqual(row["destination"], "GHL")
        self.assertEqual(row["status"], "NEEDS_SYNC")
        self.assertEqual(row["reason"], "HOT_ENGAGED")
        self.assertEqual(
            row["created_at"], NOW.isoformat(),
            "created_at must match the injected now",
        )
        self.assertEqual(
            row["updated_at"], NOW.isoformat(),
            "updated_at must match the injected now on first write",
        )

    # ------------------------------------------------------------------
    # T8 — Second call: row count stays 1; wrote=True returned both times
    # ------------------------------------------------------------------
    def test_second_call_returns_wrote_true_and_count_stays_one(self):
        """Both calls must return wrote=True and the row count must remain 1."""
        self._setup_scenario("HOT_READY")

        r1 = self._call()
        r2 = self._call()

        self.assertTrue(r1["wrote"], "First call must return wrote=True")
        self.assertTrue(r2["wrote"], "Second call must return wrote=True (update path)")
        self.assertEqual(self._sync_count(), 1, "Row count must remain 1 after two calls")


if __name__ == "__main__":
    unittest.main()
