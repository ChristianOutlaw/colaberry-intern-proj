"""
tests/test_e2e_orchestration.py

End-to-end validation: ingestion → state → scan → recommendation.

Proves the full pipeline works without mocking:
  bulk_ingest_leads()             → leads table
  create_student_invite_from_payload()  → course_invites (sent_at=NULL)
  mark_course_invite_sent()       → course_invites.sent_at = confirmed
  course_state rows (direct write) → completion_pct + last_activity_at
  run_booking_ready_scan()        → orchestration output

Two leads are inserted:
  e2e-book-001  completion=100, activity=1 day ago   → INCLUDED  (HOT window)
  e2e-no-book-002  completion=100, activity=8 days ago  → EXCLUDED (outside HOT window)
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

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.ingestion.bulk_ingest_leads import bulk_ingest_leads           # noqa: E402
from execution.leads.create_student_invite_from_payload import (              # noqa: E402
    create_student_invite_from_payload,
)
from execution.leads.mark_course_invite_sent import mark_course_invite_sent   # noqa: E402
from execution.orchestration.run_booking_ready_scan import (                  # noqa: E402
    run_booking_ready_scan,
)

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_e2e_orchestration.db")

_NOW     = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
_RECENT  = (_NOW - timedelta(days=1)).isoformat()   # inside 7-day HOT window
_OUTSIDE = (_NOW - timedelta(days=8)).isoformat()   # outside 7-day HOT window


def _write_course_state(lead_id: str, completion_pct: float, last_activity_at: str) -> None:
    """Direct DB write: upsert a course_state row for end-to-end test setup."""
    conn = connect(TEST_DB_PATH)
    conn.execute(
        """
        INSERT OR REPLACE INTO course_state
            (lead_id, course_id, started_at, completion_pct, last_activity_at, updated_at)
        VALUES (?, 'FREE_INTRO_AI_V0', ?, ?, ?, ?)
        """,
        (lead_id, "2026-01-15T00:00:00", completion_pct, last_activity_at, last_activity_at),
    )
    conn.commit()
    conn.close()


class TestE2EOrchestration(unittest.TestCase):

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

    def test_e2e_ingestion_to_recommendation(self):
        """
        Full pipeline: ingest 2 leads → set state → scan → recommendations.

        Expected:
          e2e-book-001   → INCLUDED  (completion=100, invite sent, activity 1 day ago)
          e2e-no-book-002 → EXCLUDED (completion=100, invite sent, activity 8 days ago)
        """

        # ----------------------------------------------------------------
        # Step 1 — Bulk ingestion
        # ----------------------------------------------------------------
        ingest_results = bulk_ingest_leads(
            [
                {"id": "e2e-book-001",    "name": "Alice Ready",    "email": "alice@e2e.com"},
                {"id": "e2e-no-book-002", "name": "Bob OutsideHot", "email": "bob@e2e.com"},
            ],
            db_path=TEST_DB_PATH,
        )

        print("\n--- Step 1: bulk_ingest_leads ---")
        for r in ingest_results:
            print(f"  {r}")

        self.assertEqual(len(ingest_results), 2)
        self.assertTrue(all(r["success"] for r in ingest_results), ingest_results)

        # ----------------------------------------------------------------
        # Step 2 — Create and confirm invites for both leads
        # ----------------------------------------------------------------
        for lead_id, invite_id in [
            ("e2e-book-001",    "INV-book-001"),
            ("e2e-no-book-002", "INV-no-book-002"),
        ]:
            create_student_invite_from_payload(
                lead_id=lead_id,
                invite_id=invite_id,
                db_path=TEST_DB_PATH,
            )
            mark_course_invite_sent(invite_id, lead_id, db_path=TEST_DB_PATH)

        print("\n--- Step 2: invites created and confirmed sent ---")
        conn = connect(TEST_DB_PATH)
        for lead_id in ("e2e-book-001", "e2e-no-book-002"):
            row = conn.execute(
                "SELECT id, sent_at FROM course_invites WHERE lead_id = ?", (lead_id,)
            ).fetchone()
            print(f"  lead={lead_id}  invite_id={row['id']}  sent_at={row['sent_at']}")
            self.assertIsNotNone(row["sent_at"], f"invite not marked sent for {lead_id}")
        conn.close()

        # ----------------------------------------------------------------
        # Step 3 — Write course_state: both 100% complete, different activity
        # ----------------------------------------------------------------
        _write_course_state("e2e-book-001",    100.0, _RECENT)   # 1 day ago — HOT
        _write_course_state("e2e-no-book-002", 100.0, _OUTSIDE)  # 8 days ago — outside

        print("\n--- Step 3: course_state written ---")
        conn = connect(TEST_DB_PATH)
        for lead_id in ("e2e-book-001", "e2e-no-book-002"):
            row = conn.execute(
                "SELECT completion_pct, last_activity_at FROM course_state WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
            print(f"  lead={lead_id}  completion={row['completion_pct']}%  "
                  f"last_activity_at={row['last_activity_at']}")
        conn.close()

        # ----------------------------------------------------------------
        # Step 4 — Run orchestration
        # ----------------------------------------------------------------
        output = run_booking_ready_scan(now=_NOW, db_path=TEST_DB_PATH)

        print("\n--- Step 4: run_booking_ready_scan output ---")
        for rec in output:
            print(f"  {rec}")

        # ----------------------------------------------------------------
        # Assertions
        # ----------------------------------------------------------------
        included_ids = [r["lead_id"] for r in output]

        # e2e-book-001: activity 1 day ago → inside HOT window → INCLUDED
        self.assertIn("e2e-book-001", included_ids,
                      "e2e-book-001 should be included (activity within 7-day HOT window)")

        # e2e-no-book-002: activity 8 days ago → outside HOT window → EXCLUDED
        self.assertNotIn("e2e-no-book-002", included_ids,
                         "e2e-no-book-002 should be excluded (activity outside 7-day HOT window)")

        # Included lead emits the expected event type and priority
        book_rec = next(r for r in output if r["lead_id"] == "e2e-book-001")
        self.assertEqual(book_rec["event_type"], "READY_FOR_BOOKING")
        self.assertEqual(book_rec["priority"],   "HIGH")

        print("\n--- Inclusion/Exclusion summary ---")
        print(f"  INCLUDED  e2e-book-001    activity={_RECENT}  (within 7d HOT window)")
        print(f"  EXCLUDED  e2e-no-book-002 activity={_OUTSIDE} (outside 7d HOT window)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
