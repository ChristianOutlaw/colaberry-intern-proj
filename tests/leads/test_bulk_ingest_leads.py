"""
tests/test_bulk_ingest_leads.py

Unit tests for execution/ingestion/bulk_ingest_leads.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                        # noqa: E402
from execution.ingestion.bulk_ingest_leads import bulk_ingest_leads     # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_bulk_ingest_leads.db")


class TestBulkIngestLeads(unittest.TestCase):

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
    # Scenario C — empty list → empty result, no error
    # ------------------------------------------------------------------
    def test_scenario_c_empty_list_returns_empty(self):
        """Scenario C: [] → [] with no exception."""
        result = bulk_ingest_leads([], db_path=TEST_DB_PATH)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Scenario A — valid list → all succeed
    # ------------------------------------------------------------------
    def test_scenario_a_all_valid_payloads_succeed(self):
        """Scenario A: three valid payloads → all succeed, rows in DB."""
        payloads = [
            {"id": "lead-001", "name": "Alice", "email": "alice@example.com", "phone": "5551111"},
            {"id": "lead-002", "name": "Bob",   "email": "bob@example.com"},
            {"id": "lead-003"},                                    # minimal — id only
        ]
        results = bulk_ingest_leads(payloads, db_path=TEST_DB_PATH)

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r["success"], f"Expected success for {r['lead_id']}: {r}")
            self.assertNotIn("error", r)

    def test_scenario_a_lead_ids_match_input_order(self):
        """Scenario A: result order and lead_ids match input order."""
        payloads = [{"id": "L-X"}, {"id": "L-Y"}, {"id": "L-Z"}]
        results = bulk_ingest_leads(payloads, db_path=TEST_DB_PATH)
        self.assertEqual([r["lead_id"] for r in results], ["L-X", "L-Y", "L-Z"])

    def test_scenario_a_rows_written_to_db(self):
        """Scenario A: successful ingestion writes rows to the leads table."""
        bulk_ingest_leads(
            [{"id": "db-lead", "name": "DB Test", "email": "db@test.com"}],
            db_path=TEST_DB_PATH,
        )
        conn = connect(TEST_DB_PATH)
        row = conn.execute("SELECT name, email FROM leads WHERE id = ?", ("db-lead",)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "DB Test")
        self.assertEqual(row["email"], "db@test.com")

    # ------------------------------------------------------------------
    # Scenario B — one invalid lead → partial failure, others succeed
    # ------------------------------------------------------------------
    def test_scenario_b_missing_id_fails_others_succeed(self):
        """Scenario B: middle payload missing 'id' fails; first and last succeed."""
        payloads = [
            {"id": "good-001", "name": "Good One"},
            {"name": "No ID Here"},                # missing 'id'
            {"id": "good-003", "name": "Good Three"},
        ]
        results = bulk_ingest_leads(payloads, db_path=TEST_DB_PATH)

        self.assertEqual(len(results), 3)
        self.assertTrue(results[0]["success"])
        self.assertFalse(results[1]["success"])
        self.assertIn("error", results[1])
        self.assertTrue(results[2]["success"])

    def test_scenario_b_empty_id_string_fails(self):
        """Scenario B: empty string id is invalid → failure with error message."""
        results = bulk_ingest_leads([{"id": ""}], db_path=TEST_DB_PATH)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertIn("error", results[0])

    def test_scenario_b_non_dict_payload_fails(self):
        """Scenario B: a non-dict entry fails gracefully."""
        payloads = [{"id": "valid-lead"}, "not-a-dict"]
        results = bulk_ingest_leads(payloads, db_path=TEST_DB_PATH)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["success"])
        self.assertFalse(results[1]["success"])
        self.assertIn("error", results[1])

    def test_scenario_b_error_field_is_string(self):
        """Scenario B: error value is always a non-empty string."""
        results = bulk_ingest_leads([{"id": None}], db_path=TEST_DB_PATH)
        self.assertFalse(results[0]["success"])
        self.assertIsInstance(results[0]["error"], str)
        self.assertTrue(results[0]["error"])

    # ------------------------------------------------------------------
    # Idempotency — reinserting the same id must succeed (upsert)
    # ------------------------------------------------------------------
    def test_idempotent_reingest_succeeds(self):
        """Ingesting the same lead_id twice succeeds both times (upsert semantics)."""
        payload = [{"id": "idempotent-lead", "name": "First Pass"}]
        r1 = bulk_ingest_leads(payload, db_path=TEST_DB_PATH)
        r2 = bulk_ingest_leads(payload, db_path=TEST_DB_PATH)
        self.assertTrue(r1[0]["success"])
        self.assertTrue(r2[0]["success"])


if __name__ == "__main__":
    unittest.main()
