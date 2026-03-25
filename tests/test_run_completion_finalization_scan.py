"""
tests/test_run_completion_finalization_scan.py

Unit tests for services/worker/run_completion_finalization_scan.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.run_completion_finalization_scan import run_completion_finalization_scan


def _seed_invite(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_invites (id, lead_id, course_id, sent_at)"
        " VALUES (?, ?, ?, ?)",
        (f"inv-{lead_id}", lead_id, "FREE_INTRO_AI_V0", _TS_CREATED),
    )
    conn.commit()
    conn.close()

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_run_completion_finalization_scan.db")

_TS_CREATED  = "2026-01-01T00:00:00Z"
_TS_STARTED  = "2026-02-01T00:00:00Z"
_TS_ACTIVITY = "2026-02-20T00:00:00Z"


def _seed_lead(lead_id: str) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO leads (id, name, email, phone, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "Test Lead", f"{lead_id}@test.com", "5550000000", _TS_CREATED, _TS_CREATED),
    )
    conn.commit()
    conn.close()


def _seed_course_state(lead_id: str, completion_pct: float) -> None:
    conn = connect(TEST_DB_PATH)
    conn.execute(
        "INSERT INTO course_state"
        " (lead_id, course_id, started_at, completion_pct, last_activity_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, "FREE_INTRO_AI_V0", _TS_STARTED, completion_pct, _TS_ACTIVITY, _TS_CREATED),
    )
    conn.commit()
    conn.close()


class TestRunCompletionFinalizationScan(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — empty DB -> count 0, lead_ids []
    # ------------------------------------------------------------------
    def test_t1_empty_db_returns_zero_count(self):
        """T1: no leads -> count=0, lead_ids=[]."""
        result = run_completion_finalization_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["scan_name"], "COMPLETION_FINALIZATION_SCAN")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])
        self.assertEqual(result["limit_used"], 100)
        self.assertIn("score_summary", result)
        self.assertEqual(result["score_summary"]["HAS_SCORE"], 0)
        self.assertEqual(result["score_summary"]["MISSING_SCORE"], 0)
        self.assertIn("fallback_final_label_summary", result)
        fls = result["fallback_final_label_summary"]
        self.assertEqual(fls["FINAL_COLD"], 0)
        self.assertEqual(fls["FINAL_WARM"], 0)
        self.assertEqual(fls["FINAL_HOT"],  0)
        self.assertIn("enrichment_summary", result)
        es = result["enrichment_summary"]
        self.assertEqual(es["INVITE_SENT_TRUE"], 0)
        self.assertEqual(es["INVITE_SENT_FALSE"], 0)
        self.assertEqual(es["QUIZ_DATA_PRESENT"], 0)
        self.assertEqual(es["QUIZ_DATA_MISSING"], 0)
        self.assertEqual(es["REFLECTION_DATA_PRESENT"], 0)
        self.assertEqual(es["REFLECTION_DATA_MISSING"], 0)

    # ------------------------------------------------------------------
    # T2 — one completed lead -> count 1 and correct lead_id
    # ------------------------------------------------------------------
    def test_t2_one_completed_lead(self):
        """T2: completion_pct=100 -> count=1, correct lead_id in list."""
        _seed_lead("lead-a")
        _seed_course_state("lead-a", completion_pct=100.0)
        result = run_completion_finalization_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn("lead-a", result["lead_ids"])
        self.assertIn("score_summary", result)
        self.assertEqual(result["score_summary"]["HAS_SCORE"] + result["score_summary"]["MISSING_SCORE"], result["count"])
        self.assertEqual(result["score_summary"]["MISSING_SCORE"], 1)
        self.assertIn("fallback_final_label_summary", result)
        fls = result["fallback_final_label_summary"]
        self.assertEqual(sum(fls.values()), result["count"])
        # scan rows have no hot_signal; current fallback lands all in FINAL_WARM
        self.assertEqual(fls["FINAL_WARM"], 1)
        self.assertEqual(fls["FINAL_HOT"],  0)
        self.assertIn("enrichment_summary", result)
        es = result["enrichment_summary"]
        self.assertEqual(es["INVITE_SENT_TRUE"] + es["INVITE_SENT_FALSE"], result["count"])
        self.assertEqual(es["QUIZ_DATA_PRESENT"] + es["QUIZ_DATA_MISSING"], result["count"])
        self.assertEqual(es["REFLECTION_DATA_PRESENT"] + es["REFLECTION_DATA_MISSING"], result["count"])
        self.assertEqual(es["INVITE_SENT_FALSE"], 1)
        self.assertEqual(es["QUIZ_DATA_MISSING"], 1)
        self.assertEqual(es["REFLECTION_DATA_MISSING"], 1)

    # ------------------------------------------------------------------
    # T3 — incomplete lead -> excluded
    # ------------------------------------------------------------------
    def test_t3_incomplete_lead_excluded(self):
        """T3: completion_pct=60 -> excluded from results."""
        _seed_lead("lead-b")
        _seed_course_state("lead-b", completion_pct=60.0)
        result = run_completion_finalization_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["lead_ids"], [])

    # ------------------------------------------------------------------
    # T4 — limit respected
    # ------------------------------------------------------------------
    def test_t4_limit_respected(self):
        """T4: three completed leads, limit=2 -> count=2."""
        for i in ("lead-x", "lead-y", "lead-z"):
            _seed_lead(i)
            _seed_course_state(i, completion_pct=100.0)
        result = run_completion_finalization_scan(limit=2, db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["lead_ids"]), 2)
        self.assertEqual(result["limit_used"], 2)
        self.assertEqual(result["score_summary"]["HAS_SCORE"] + result["score_summary"]["MISSING_SCORE"], result["count"])
        fls = result["fallback_final_label_summary"]
        self.assertEqual(sum(fls.values()), result["count"])


    # ------------------------------------------------------------------
    # T5 — completed lead with sent invite -> INVITE_SENT_TRUE == 1
    # ------------------------------------------------------------------
    def test_t5_invite_sent_counted_in_enrichment_summary(self):
        """T5: completed lead with sent invite -> enrichment_summary INVITE_SENT_TRUE=1."""
        _seed_lead("lead-e")
        _seed_course_state("lead-e", completion_pct=100.0)
        _seed_invite("lead-e")
        result = run_completion_finalization_scan(db_path=TEST_DB_PATH)
        self.assertEqual(result["count"], 1)
        self.assertIn("enrichment_summary", result)
        es = result["enrichment_summary"]
        self.assertEqual(es["INVITE_SENT_TRUE"], 1)
        self.assertEqual(es["INVITE_SENT_FALSE"], 0)
        self.assertEqual(es["QUIZ_DATA_MISSING"], 1)
        self.assertEqual(es["REFLECTION_DATA_MISSING"], 1)


if __name__ == "__main__":
    unittest.main()
