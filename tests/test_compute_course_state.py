"""
tests/test_compute_course_state.py

Unit tests for execution/progress/compute_course_state.py.
Uses an isolated database (tmp/test_course_state.db) and never touches
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

from execution.db.sqlite import connect, init_db                            # noqa: E402
from execution.leads.upsert_lead import upsert_lead                         # noqa: E402
from execution.progress.record_progress_event import record_progress_event  # noqa: E402
from execution.progress.compute_course_state import compute_course_state    # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_course_state.db")


def _fetch_course_state(lead_id: str) -> dict:
    """Return the course_state row for a lead as a plain dict, or {} if missing."""
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM course_state WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


class TestComputeCourseState(unittest.TestCase):

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
    # Test 1 — no events -> no course_state row
    # ------------------------------------------------------------------
    def test_no_events_creates_no_course_state(self):
        """compute_course_state must not write a row when the lead has no events."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)

        row = _fetch_course_state("L1")
        self.assertEqual(row, {}, "Expected no course_state row when lead has no events")

    # ------------------------------------------------------------------
    # Test 2 — events present -> correct row inserted
    # ------------------------------------------------------------------
    def test_inserts_course_state_from_events(self):
        """compute_course_state must insert a row with correct derived values."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E2", "L1", "P1_S2",
            occurred_at="2026-01-02T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)

        row = _fetch_course_state("L1")

        self.assertNotEqual(row, {}, "Expected a course_state row to be created")
        self.assertEqual(row["current_section"], "P1_S2")
        self.assertEqual(row["last_activity_at"], "2026-01-02T00:00:00+00:00")
        self.assertAlmostEqual(row["completion_pct"], 20.0, places=5)

    # ------------------------------------------------------------------
    # Test 3 — second compute updates existing row
    # ------------------------------------------------------------------
    def test_updates_existing_course_state(self):
        """A second call to compute_course_state must update the existing row."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E2", "L1", "P1_S2",
            occurred_at="2026-01-02T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)

        first = _fetch_course_state("L1")
        first_updated_at = first["updated_at"]

        record_progress_event(
            "E3", "L1", "P1_S3",
            occurred_at="2026-01-03T00:00:00+00:00",
            db_path=TEST_DB_PATH,
        )
        compute_course_state("L1", total_sections=10, db_path=TEST_DB_PATH)

        second = _fetch_course_state("L1")

        self.assertEqual(second["current_section"], "P1_S3")
        self.assertAlmostEqual(second["completion_pct"], 30.0, places=5)
        self.assertNotEqual(
            second["updated_at"],
            first_updated_at,
            "updated_at must change when course_state is recomputed",
        )


if __name__ == "__main__":
    unittest.main()
