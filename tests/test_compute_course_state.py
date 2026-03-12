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
from unittest.mock import patch

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


def _fetch_course_state(
    lead_id: str,
    course_id: str = "FREE_INTRO_AI_V0",
) -> dict:
    """Return the course_state row for a (lead, course) pair as a plain dict, or {} if missing."""
    conn = connect(TEST_DB_PATH)
    try:
        row = conn.execute(
            "SELECT * FROM course_state WHERE lead_id = ? AND course_id = ?",
            (lead_id, course_id),
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
        self.assertEqual(row["course_id"], "FREE_INTRO_AI_V0")
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


    # ------------------------------------------------------------------
    # Test 4 — course_id scoping: events from another course are excluded
    # ------------------------------------------------------------------
    def test_course_id_scoping(self):
        """compute_course_state must only count events matching the given course_id."""
        upsert_lead("L1", db_path=TEST_DB_PATH)

        # Two events for the target course.
        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            course_id="FREE_INTRO_AI_V0",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E2", "L1", "P1_S2",
            occurred_at="2026-01-02T00:00:00+00:00",
            course_id="FREE_INTRO_AI_V0",
            db_path=TEST_DB_PATH,
        )
        # One event for a different course — must not be counted.
        record_progress_event(
            "E3", "L1", "P1_S3",
            occurred_at="2026-01-03T00:00:00+00:00",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        compute_course_state(
            "L1", total_sections=10,
            course_id="FREE_INTRO_AI_V0",
            db_path=TEST_DB_PATH,
        )
        row = _fetch_course_state("L1")

        self.assertEqual(row["current_section"], "P1_S2",
                         "current_section must reflect only FREE_INTRO_AI_V0 events")
        self.assertAlmostEqual(row["completion_pct"], 20.0, places=5,
                               msg="completion_pct must not count events from other courses")

    # ------------------------------------------------------------------
    # Test 5 — two courses produce two independent course_state rows
    # ------------------------------------------------------------------
    def test_two_courses_produce_separate_rows(self):
        """Computing state for two different courses must write two separate rows
        that do not overwrite each other."""
        upsert_lead("L1", db_path=TEST_DB_PATH)

        record_progress_event(
            "E1", "L1", "P1_S1",
            occurred_at="2026-01-01T00:00:00+00:00",
            course_id="FREE_INTRO_AI_V0",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E2", "L1", "P1_S1",
            occurred_at="2026-01-02T00:00:00+00:00",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )
        record_progress_event(
            "E3", "L1", "P1_S2",
            occurred_at="2026-01-03T00:00:00+00:00",
            course_id="OTHER_COURSE_V1",
            db_path=TEST_DB_PATH,
        )

        compute_course_state("L1", total_sections=10,
                             course_id="FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        compute_course_state("L1", total_sections=10,
                             course_id="OTHER_COURSE_V1", db_path=TEST_DB_PATH)

        row_a = _fetch_course_state("L1", "FREE_INTRO_AI_V0")
        row_b = _fetch_course_state("L1", "OTHER_COURSE_V1")

        self.assertNotEqual(row_a, {}, "Expected course_state row for FREE_INTRO_AI_V0")
        self.assertNotEqual(row_b, {}, "Expected course_state row for OTHER_COURSE_V1")
        self.assertEqual(row_a["current_section"], "P1_S1")
        self.assertEqual(row_b["current_section"], "P1_S2")
        self.assertAlmostEqual(row_a["completion_pct"], 10.0, places=5)
        self.assertAlmostEqual(row_b["completion_pct"], 20.0, places=5)

        # Verify two physical rows exist in the DB.
        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM course_state WHERE lead_id = ?", ("L1",)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2, "Expected exactly two course_state rows for lead L1")


class TestComputeCourseStateWebhook(unittest.TestCase):
    """Tests for the outbound course_completed webhook emission."""

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def _seed_events(self, lead_id, sections, course_id="FREE_INTRO_AI_V0"):
        """Helper: seed one progress event per section for the given lead."""
        for i, section in enumerate(sections):
            record_progress_event(
                f"E{i}", lead_id, section,
                occurred_at=f"2026-01-0{i + 1}T00:00:00+00:00",
                course_id=course_id,
                db_path=TEST_DB_PATH,
            )

    # ------------------------------------------------------------------
    # Test 6 — no webhook call when webhook_url is absent
    # ------------------------------------------------------------------
    def test_no_webhook_call_when_url_absent(self):
        """send_course_event must not be called when webhook_url is not supplied."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        self._seed_events("L1", ["P1_S1", "P1_S2"])

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            compute_course_state("L1", total_sections=2, db_path=TEST_DB_PATH)

        mock_send.assert_not_called()

    # ------------------------------------------------------------------
    # Test 7 — no webhook call when completion_pct < 100
    # ------------------------------------------------------------------
    def test_no_webhook_call_when_not_complete(self):
        """send_course_event must not be called when completion_pct < 100."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        self._seed_events("L1", ["P1_S1"])  # 1 of 10 = 10%

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            compute_course_state(
                "L1", total_sections=10,
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )

        mock_send.assert_not_called()

    # ------------------------------------------------------------------
    # Test 8 — webhook fired with correct args when completion reaches 100
    # ------------------------------------------------------------------
    def test_webhook_called_on_completion(self):
        """send_course_event must be called once with 'course_completed' and
        correct payload when completion_pct first reaches 100 %."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        self._seed_events("L1", ["P1_S1", "P1_S2"])  # 2 of 2 = 100%

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            mock_send.return_value = {"status": "success", "http_status": 200, "error": None}
            compute_course_state(
                "L1", total_sections=2,
                course_id="FREE_INTRO_AI_V0",
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )

        mock_send.assert_called_once_with(
            "course_completed",
            {"lead_id": "L1", "course_id": "FREE_INTRO_AI_V0", "completion_pct": 100.0},
            webhook_url="http://example.com/hook",
        )

    # ------------------------------------------------------------------
    # Test 9 — transition guard: no re-fire on second call at 100 %
    # ------------------------------------------------------------------
    def test_no_duplicate_webhook_on_recompute_of_completed_course(self):
        """A second compute_course_state call when the course is already at
        100 % must not re-fire the webhook."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        self._seed_events("L1", ["P1_S1", "P1_S2"])  # 2 of 2 = 100%

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            mock_send.return_value = {"status": "success", "http_status": 200, "error": None}
            # First call — should fire.
            compute_course_state(
                "L1", total_sections=2,
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )
            # Second call — prev_completion_pct is now 100.0; must not fire again.
            compute_course_state(
                "L1", total_sections=2,
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )

        self.assertEqual(
            mock_send.call_count, 1,
            "send_course_event must fire exactly once, not on repeated recomputation",
        )

    # ------------------------------------------------------------------
    # Test 10 — webhook failure does not break state write
    # ------------------------------------------------------------------
    def test_webhook_failure_does_not_break_state_write(self):
        """A failed outbound webhook must not prevent the course_state row
        from being written or updated correctly."""
        upsert_lead("L1", db_path=TEST_DB_PATH)
        self._seed_events("L1", ["P1_S1", "P1_S2"])  # 100%

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            mock_send.return_value = {"status": "error", "http_status": None, "error": "refused"}
            compute_course_state(
                "L1", total_sections=2,
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )

        row = _fetch_course_state("L1")
        self.assertNotEqual(row, {}, "course_state row must exist despite webhook failure")
        self.assertAlmostEqual(row["completion_pct"], 100.0, places=5)

    # ------------------------------------------------------------------
    # Test 11 — no webhook when lead has no events (early return path)
    # ------------------------------------------------------------------
    def test_no_webhook_when_no_events(self):
        """compute_course_state must not call send_course_event when the
        lead has no progress events (early-return path)."""
        upsert_lead("L1", db_path=TEST_DB_PATH)

        with patch(
            "execution.progress.compute_course_state.send_course_event"
        ) as mock_send:
            compute_course_state(
                "L1", total_sections=2,
                webhook_url="http://example.com/hook",
                db_path=TEST_DB_PATH,
            )

        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
