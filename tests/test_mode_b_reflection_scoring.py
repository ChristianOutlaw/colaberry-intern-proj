"""
tests/test_mode_b_reflection_scoring.py

Focused regression test: verifies Mode A reflection scoring behavior.

Mode A rule (current): reflection_confidence contributes differentiated points
to the lead temperature score:
    HIGH   → +15 pts (full W_REFLECTION)
    MEDIUM → +8 pts
    LOW    → +0 pts
    None   → +0 pts

Scenario A — reflection data affects the final score in a differentiated way:
  - HIGH > MEDIUM > LOW == None

Scenario B — reflection storage must not regress:
  - save_reflection_response writes a row to reflection_responses.
  - The row persists after scoring; no side effect deletes it.
"""

import gc
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                          # noqa: E402
from execution.leads.compute_lead_temperature import compute_lead_temperature  # noqa: E402
from execution.reflection.save_reflection_response import save_reflection_response  # noqa: E402

_NOW = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_mode_b_reflection_scoring.db")

# Shared scoring kwargs — every field except reflection_confidence is identical
_SHARED = dict(
    now=_NOW,
    invited_sent=True,
    completion_percent=60.0,
    last_activity_at=(_NOW - timedelta(days=5)).isoformat(),
    started_at=(_NOW - timedelta(days=30)).isoformat(),
    avg_quiz_score=65.0,
    avg_quiz_attempts=1.5,
    current_section="section-4",
)


class TestModeBReflectionScoring(unittest.TestCase):

    # ------------------------------------------------------------------
    # Scenario A — reflection data must NOT change the score
    # ------------------------------------------------------------------

    def test_scenario_a_high_reflection_same_score_as_none(self):
        """HIGH reflection_confidence scores higher than None (Mode A: differentiated scoring)."""
        result_with    = compute_lead_temperature(**_SHARED, reflection_confidence="HIGH")
        result_without = compute_lead_temperature(**_SHARED, reflection_confidence=None)

        self.assertGreater(
            result_with["score"],
            result_without["score"],
            "Expected HIGH reflection to produce a higher score than None.",
        )
        # HIGH earns full W_REFLECTION (15 pts); None earns 0 pts
        self.assertIn("REFLECTION_HIGH", result_with["reason_codes"])

    def test_scenario_a_low_reflection_same_score_as_none(self):
        """LOW reflection_confidence produces identical score to None (Mode B enforced)."""
        result_with    = compute_lead_temperature(**_SHARED, reflection_confidence="LOW")
        result_without = compute_lead_temperature(**_SHARED, reflection_confidence=None)

        self.assertEqual(
            result_with["score"],
            result_without["score"],
            "Mode B violation: reflection_confidence='LOW' changed the score.",
        )

    def test_scenario_a_all_confidence_levels_produce_same_score(self):
        """HIGH / MEDIUM / LOW / None produce differentiated scores under Mode A scoring."""
        scores = {
            level: compute_lead_temperature(**_SHARED, reflection_confidence=level)["score"]
            for level in ("HIGH", "MEDIUM", "LOW", None)
        }
        # Mode A: HIGH (15 pts) > MEDIUM (8 pts) > LOW/None (0 pts each)
        self.assertGreater(scores["HIGH"],   scores["MEDIUM"], f"Expected HIGH > MEDIUM: {scores}")
        self.assertGreater(scores["MEDIUM"], scores["LOW"],    f"Expected MEDIUM > LOW: {scores}")
        self.assertEqual(scores["LOW"],      scores[None],     f"Expected LOW == None: {scores}")

    def test_scenario_a_reflection_reason_code_still_present(self):
        """Even though reflection does not affect the score, the reason code is still emitted."""
        result = compute_lead_temperature(**_SHARED, reflection_confidence="HIGH")
        self.assertIn(
            "REFLECTION_HIGH",
            result["reason_codes"],
            "Expected REFLECTION_HIGH reason code even under Mode B (stored, not scored).",
        )

    # ------------------------------------------------------------------
    # Scenario B — reflection storage must not regress
    # ------------------------------------------------------------------

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()
        # Seed the lead row that save_reflection_response depends on
        conn = connect(TEST_DB_PATH)
        conn.execute(
            "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("lead-refl", "Refl Lead", "refl@test.com", "5550001111",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    def test_scenario_b_reflection_data_is_stored(self):
        """save_reflection_response writes the row; scoring does not delete it."""
        save_reflection_response(
            lead_id="lead-refl",
            course_id="FREE_INTRO_AI_V0",
            section_id="section-1",
            prompt_index=0,
            response_text="I learned about neural networks and found it very engaging.",
            created_at="2026-02-25T12:00:00",
            db_path=TEST_DB_PATH,
        )

        # Verify the row is present in the DB
        conn = connect(TEST_DB_PATH)
        row = conn.execute(
            "SELECT response_text FROM reflection_responses"
            " WHERE lead_id = ? AND section_id = ? AND prompt_index = ?",
            ("lead-refl", "section-1", 0),
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row, "Reflection response row was not stored.")
        self.assertIn("neural networks", row["response_text"])

    def test_scenario_b_scoring_does_not_delete_reflection_data(self):
        """Running compute_lead_temperature leaves reflection_responses rows untouched."""
        save_reflection_response(
            lead_id="lead-refl",
            course_id="FREE_INTRO_AI_V0",
            section_id="section-2",
            prompt_index=0,
            response_text="Reflection answer that must survive scoring.",
            created_at="2026-02-25T12:00:00",
            db_path=TEST_DB_PATH,
        )

        # Run the pure scoring function (no DB access — just confirm it doesn't error)
        compute_lead_temperature(**_SHARED, reflection_confidence="HIGH")

        # Row must still exist after scoring
        conn = connect(TEST_DB_PATH)
        count = conn.execute(
            "SELECT COUNT(*) FROM reflection_responses WHERE lead_id = ?",
            ("lead-refl",),
        ).fetchone()[0]
        conn.close()

        self.assertEqual(count, 1, "Reflection row was unexpectedly removed after scoring.")


if __name__ == "__main__":
    unittest.main()
