"""
tests/test_admin_simulate_scenario.py

Unit tests for execution/admin/simulate_scenario.py.

Covers:
    S1  — COLD_NO_INVITE     → lead exists, no invite, no progress events
    S2  — INVITED_NO_PROGRESS → invite row exists, no progress events
    S3  — PARTIAL_PROGRESS   → exactly 3 progress_events rows
    S4  — FULL_COMPLETION    → exactly 9 progress_events rows
    S5  — HOT_READY          → get_lead_status reports hot signal = "HOT"
    S6  — STALE_ACTIVITY     → get_lead_status reports hot=False, reason ACTIVITY_STALE
    S7  — Unknown scenario_id → raises ValueError before any DB write
    S8  — confirm=False       → raises OperationNotConfirmedError before any DB write
    +   — Idempotency: running a scenario twice produces the same final state

Uses an isolated database (tmp/test_admin_simulate_scenario.db) and never
touches the application database (tmp/app.db).
All fixture data uses deterministic IDs.  All timestamps are injected via `now`.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — repo root must be importable from any test runner.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.admin.reset_progress import OperationNotConfirmedError  # noqa: E402
from execution.admin.simulate_scenario import simulate_scenario         # noqa: E402
from execution.db.sqlite import connect, init_db                        # noqa: E402
from execution.leads.get_lead_status import get_lead_status             # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_admin_simulate_scenario.db")

# ---------------------------------------------------------------------------
# Fixture constants — deterministic; never changed between test runs.
# ---------------------------------------------------------------------------
LEAD_ID = "SIM_LEAD_01"

# Injected "now": Feb 25 2026 12:00 UTC — matches today per CLAUDE.md context.
# HOT_READY events stored at this time are 0 days old → hot=True when evaluated
# by get_lead_status at real current time (also Feb 25 2026).
NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# DB inspection helpers
# ---------------------------------------------------------------------------

def _lead_exists() -> bool:
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT id FROM leads WHERE id = ?", (LEAD_ID,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _invite_count() -> int:
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM course_invites WHERE lead_id = ?", (LEAD_ID,)
        ).fetchone()[0]
    finally:
        conn.close()


def _progress_count() -> int:
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM progress_events WHERE lead_id = ?", (LEAD_ID,)
        ).fetchone()[0]
    finally:
        conn.close()


def _total_leads() -> int:
    conn = connect(TEST_DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    finally:
        conn.close()


class TestSimulateScenario(unittest.TestCase):

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

    def _run(self, scenario_id: str, **kwargs) -> dict:
        """Call simulate_scenario with the shared test defaults."""
        return simulate_scenario(
            scenario_id=scenario_id,
            lead_id=LEAD_ID,
            confirm=True,
            now=NOW,
            db_path=TEST_DB_PATH,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # S7 — Unknown scenario_id raises ValueError before any write
    # ------------------------------------------------------------------
    def test_unknown_scenario_raises_value_error_before_write(self):
        """An unknown scenario_id must raise ValueError and write nothing."""
        with self.assertRaises(ValueError) as ctx:
            simulate_scenario(
                scenario_id="TOTALLY_UNKNOWN",
                lead_id=LEAD_ID,
                confirm=True,
                now=NOW,
                db_path=TEST_DB_PATH,
            )
        self.assertIn("TOTALLY_UNKNOWN", str(ctx.exception))
        self.assertEqual(_total_leads(), 0, "No DB write must occur for an unknown scenario")

    # ------------------------------------------------------------------
    # S8 — confirm=False raises OperationNotConfirmedError before write
    # ------------------------------------------------------------------
    def test_confirm_false_raises_and_does_not_write(self):
        """confirm=False must raise OperationNotConfirmedError before any DB access."""
        with self.assertRaises(OperationNotConfirmedError):
            simulate_scenario(
                scenario_id="COLD_NO_INVITE",
                lead_id=LEAD_ID,
                confirm=False,
                now=NOW,
                db_path=TEST_DB_PATH,
            )
        self.assertEqual(_total_leads(), 0, "No DB write must occur when confirm=False")

    # ------------------------------------------------------------------
    # S1 — COLD_NO_INVITE
    # ------------------------------------------------------------------
    def test_cold_no_invite(self):
        """COLD_NO_INVITE must create the lead with no invite and no progress."""
        result = self._run("COLD_NO_INVITE")

        self.assertTrue(result["ok"])
        self.assertIn("COLD_NO_INVITE", result["message"])
        self.assertIn(LEAD_ID, result["message"])

        self.assertTrue(_lead_exists(), "Lead row must be present")
        self.assertEqual(_invite_count(), 0, "No course_invites row must exist")
        self.assertEqual(_progress_count(), 0, "No progress_events row must exist")

    # ------------------------------------------------------------------
    # S2 — INVITED_NO_PROGRESS
    # ------------------------------------------------------------------
    def test_invited_no_progress(self):
        """INVITED_NO_PROGRESS must create the lead with one invite and no progress."""
        result = self._run("INVITED_NO_PROGRESS")

        self.assertTrue(result["ok"])
        self.assertTrue(_lead_exists())
        self.assertEqual(_invite_count(), 1, "Exactly one course_invites row must exist")
        self.assertEqual(_progress_count(), 0, "No progress_events row must exist")

    # ------------------------------------------------------------------
    # S3 — PARTIAL_PROGRESS
    # ------------------------------------------------------------------
    def test_partial_progress(self):
        """PARTIAL_PROGRESS must create lead + invite + exactly 3 progress events."""
        result = self._run("PARTIAL_PROGRESS")

        self.assertTrue(result["ok"])
        self.assertTrue(_lead_exists())
        self.assertEqual(_invite_count(), 1)
        self.assertEqual(
            _progress_count(), 3,
            "Exactly 3 progress_events rows must exist for PARTIAL_PROGRESS",
        )

    # ------------------------------------------------------------------
    # S4 — FULL_COMPLETION
    # ------------------------------------------------------------------
    def test_full_completion(self):
        """FULL_COMPLETION must create lead + invite + exactly 9 progress events."""
        result = self._run("FULL_COMPLETION")

        self.assertTrue(result["ok"])
        self.assertTrue(_lead_exists())
        self.assertEqual(_invite_count(), 1)
        self.assertEqual(
            _progress_count(), 9,
            "Exactly 9 progress_events rows must exist for FULL_COMPLETION",
        )

        # Verify completion_pct via get_lead_status.
        status = get_lead_status(LEAD_ID, db_path=TEST_DB_PATH)
        self.assertEqual(
            status["course_state"]["completion_pct"], 100.0,
            "completion_pct must be 100.0 after FULL_COMPLETION",
        )

    # ------------------------------------------------------------------
    # S5 — HOT_READY → hot signal = "HOT"
    # ------------------------------------------------------------------
    def test_hot_ready_reports_hot_true(self):
        """HOT_READY must result in get_lead_status reporting hot signal = 'HOT'."""
        self._run("HOT_READY")

        status = get_lead_status(LEAD_ID, db_path=TEST_DB_PATH)

        self.assertTrue(
            status["lead_exists"],
            "Lead must exist after HOT_READY scenario",
        )
        self.assertTrue(
            status["invite_sent"],
            "invite_sent must be True after HOT_READY scenario",
        )
        self.assertEqual(
            status["hot_lead"]["signal"], "HOT",
            f"Expected hot signal 'HOT', got {status['hot_lead']['signal']!r}. "
            f"reason={status['hot_lead']['reason']!r}, "
            f"completion_pct={status['course_state']['completion_pct']!r}, "
            f"last_activity_at={status['course_state']['last_activity_at']!r}",
        )

    # ------------------------------------------------------------------
    # S6 — STALE_ACTIVITY → hot=False, reason ACTIVITY_STALE
    # ------------------------------------------------------------------
    def test_stale_activity_reports_activity_stale(self):
        """STALE_ACTIVITY must result in hot=False with reason ACTIVITY_STALE."""
        self._run("STALE_ACTIVITY")

        status = get_lead_status(LEAD_ID, db_path=TEST_DB_PATH)

        self.assertEqual(
            status["hot_lead"]["signal"], "NOT_HOT",
            f"Expected NOT_HOT, got {status['hot_lead']['signal']!r}",
        )
        self.assertEqual(
            status["hot_lead"]["reason"], "ACTIVITY_STALE",
            f"Expected ACTIVITY_STALE, got {status['hot_lead']['reason']!r}. "
            f"last_activity_at={status['course_state']['last_activity_at']!r}",
        )

    # ------------------------------------------------------------------
    # Extra — Idempotency: running the same scenario twice is safe
    # ------------------------------------------------------------------
    def test_idempotent_second_run_replaces_state_cleanly(self):
        """A second call to simulate_scenario must reset then re-apply, not stack."""
        self._run("PARTIAL_PROGRESS")
        self.assertEqual(_progress_count(), 3)

        # Run again — reset should fire and leave exactly 3 events (not 6).
        self._run("PARTIAL_PROGRESS")
        self.assertEqual(
            _progress_count(), 3,
            "Second run must not stack events; exactly 3 must remain",
        )
        self.assertEqual(_invite_count(), 1, "Exactly one invite row must remain")

    # ------------------------------------------------------------------
    # Extra — Scenario transition: FULL_COMPLETION → COLD_NO_INVITE
    # ------------------------------------------------------------------
    def test_scenario_transition_clears_prior_state(self):
        """Switching from FULL_COMPLETION to COLD_NO_INVITE must wipe all prior rows."""
        self._run("FULL_COMPLETION")
        self.assertEqual(_progress_count(), 9)
        self.assertEqual(_invite_count(), 1)

        self._run("COLD_NO_INVITE")
        self.assertEqual(_progress_count(), 0, "Progress events must be cleared on transition")
        self.assertEqual(_invite_count(), 0, "Invite rows must be cleared on transition")
        self.assertTrue(_lead_exists(), "Lead row must survive the transition")

    # ------------------------------------------------------------------
    # Extra — Empty lead_id returns ok=False without writing
    # ------------------------------------------------------------------
    def test_empty_lead_id_returns_ok_false(self):
        """A blank lead_id must return ok=False and write nothing."""
        result = simulate_scenario(
            scenario_id="COLD_NO_INVITE",
            lead_id="   ",
            confirm=True,
            now=NOW,
            db_path=TEST_DB_PATH,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Lead ID is required.")
        self.assertEqual(_total_leads(), 0)


if __name__ == "__main__":
    unittest.main()
