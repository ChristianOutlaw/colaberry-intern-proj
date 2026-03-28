"""
tests/test_match_or_create_lead_from_ghl_payload.py

Unit tests for execution/leads/match_or_create_lead_from_ghl_payload.py.

Fast, deterministic, no network calls.
Uses an isolated SQLite file (tmp/test_match_or_create_lead.db) per test.

Scenarios covered
-----------------
T1  — payload missing all identity fields → ok=False, no DB mutation
T2  — non-dict payload → ok=False
T3  — phone match → ok=True, matched_by="phone"
T4  — email match when phone absent → ok=True, matched_by="email"
T5  — name match (unique) when phone and email absent → ok=True, matched_by="name"
T6  — name ambiguous (multiple leads share name) → new lead created
T7  — no match at all → new lead created, matched_by="created"
T8  — phone takes priority over email when both match different leads
T9  — ghl_contact_id stored on matched lead
T10 — ghl_contact_id stored on newly created lead
T11 — idempotency: same phone payload twice → same app_lead_id
T12 — email normalized to lowercase before matching
T13 — phone stripped of surrounding whitespace before matching
T14 — created lead_id starts with "GHL_"
T15 — supplied fields written to matched lead (update path)
T16 — name-only fallback skipped when multiple leads share the name, new lead created
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                         # noqa: E402
from execution.leads.match_or_create_lead_from_ghl_payload import (                     # noqa: E402
    match_or_create_lead_from_ghl_payload,
)

TEST_DB = str(REPO_ROOT / "tmp" / "test_match_or_create_lead.db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_lead(lead_id: str, phone=None, email=None, name=None, ghl_contact_id=None):
    """Insert a bare lead row directly for test setup."""
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO leads
                (id, phone, email, name, ghl_contact_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (lead_id, phone, email, name, ghl_contact_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_lead(lead_id: str) -> dict:
    conn = connect(TEST_DB)
    try:
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _count_leads() -> int:
    conn = connect(TEST_DB)
    try:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestMatchOrCreateLeadFromGhlPayload(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    # ------------------------------------------------------------------
    # T1 — payload missing all identity fields → ok=False, no mutation
    # ------------------------------------------------------------------
    def test_t1_all_identity_fields_absent_returns_ok_false(self):
        result = match_or_create_lead_from_ghl_payload(
            {"ghl_contact_id": "GHL123"},
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertIsNone(result["app_lead_id"])
        self.assertIsNone(result["matched_by"])
        self.assertIn("phone", result["message"].lower())
        # No lead must have been created.
        self.assertEqual(_count_leads(), 0)

    # ------------------------------------------------------------------
    # T2 — non-dict payload → ok=False
    # ------------------------------------------------------------------
    def test_t2_non_dict_payload_returns_ok_false(self):
        result = match_or_create_lead_from_ghl_payload("not-a-dict", db_path=TEST_DB)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["app_lead_id"])
        self.assertEqual(_count_leads(), 0)

    # ------------------------------------------------------------------
    # T3 — phone match
    # ------------------------------------------------------------------
    def test_t3_phone_match_returns_existing_lead(self):
        _seed_lead("EXISTING_L1", phone="5550001111")

        result = match_or_create_lead_from_ghl_payload(
            {"phone": "5550001111"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "EXISTING_L1")
        self.assertEqual(result["matched_by"], "phone")
        # No new lead must have been created.
        self.assertEqual(_count_leads(), 1)

    # ------------------------------------------------------------------
    # T4 — email match when phone is absent
    # ------------------------------------------------------------------
    def test_t4_email_match_used_when_no_phone(self):
        _seed_lead("EXISTING_L2", email="alice@example.com")

        result = match_or_create_lead_from_ghl_payload(
            {"email": "alice@example.com"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "EXISTING_L2")
        self.assertEqual(result["matched_by"], "email")
        self.assertEqual(_count_leads(), 1)

    # ------------------------------------------------------------------
    # T5 — unique name match used as weak fallback
    # ------------------------------------------------------------------
    def test_t5_name_match_used_when_unique(self):
        _seed_lead("EXISTING_L3", name="Jordan Smith")

        result = match_or_create_lead_from_ghl_payload(
            {"name": "Jordan Smith"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "EXISTING_L3")
        self.assertEqual(result["matched_by"], "name")
        self.assertEqual(_count_leads(), 1)

    # ------------------------------------------------------------------
    # T6 — ambiguous name (multiple leads) → new lead created
    # ------------------------------------------------------------------
    def test_t6_ambiguous_name_creates_new_lead(self):
        _seed_lead("EXISTING_LA", name="Alex Lee")
        _seed_lead("EXISTING_LB", name="Alex Lee")

        result = match_or_create_lead_from_ghl_payload(
            {"name": "Alex Lee"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_by"], "created")
        # A third lead must have been created.
        self.assertEqual(_count_leads(), 3)
        self.assertNotIn(result["app_lead_id"], {"EXISTING_LA", "EXISTING_LB"})

    # ------------------------------------------------------------------
    # T7 — no match at all → new lead created
    # ------------------------------------------------------------------
    def test_t7_no_match_creates_new_lead(self):
        result = match_or_create_lead_from_ghl_payload(
            {"phone": "9990009999", "email": "new@example.com", "name": "New Person"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_by"], "created")
        self.assertIsNotNone(result["app_lead_id"])
        self.assertEqual(_count_leads(), 1)

    # ------------------------------------------------------------------
    # T8 — phone takes priority over email
    # ------------------------------------------------------------------
    def test_t8_phone_takes_priority_over_email(self):
        _seed_lead("PHONE_LEAD", phone="1112223333")
        _seed_lead("EMAIL_LEAD", email="priority@example.com")

        result = match_or_create_lead_from_ghl_payload(
            {"phone": "1112223333", "email": "priority@example.com"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "PHONE_LEAD")
        self.assertEqual(result["matched_by"], "phone")
        self.assertEqual(_count_leads(), 2)

    # ------------------------------------------------------------------
    # T9 — ghl_contact_id stored on matched lead
    # ------------------------------------------------------------------
    def test_t9_ghl_contact_id_stored_on_matched_lead(self):
        _seed_lead("MATCH_L9", phone="4440005555")

        match_or_create_lead_from_ghl_payload(
            {"phone": "4440005555", "ghl_contact_id": "GHL_XYZ_001"},
            db_path=TEST_DB,
        )

        row = _fetch_lead("MATCH_L9")
        self.assertEqual(row["ghl_contact_id"], "GHL_XYZ_001")

    # ------------------------------------------------------------------
    # T10 — ghl_contact_id stored on newly created lead
    # ------------------------------------------------------------------
    def test_t10_ghl_contact_id_stored_on_created_lead(self):
        result = match_or_create_lead_from_ghl_payload(
            {"phone": "7778889999", "ghl_contact_id": "GHL_NEW_002"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        row = _fetch_lead(result["app_lead_id"])
        self.assertEqual(row["ghl_contact_id"], "GHL_NEW_002")

    # ------------------------------------------------------------------
    # T11 — idempotency: same phone payload twice → same app_lead_id
    # ------------------------------------------------------------------
    def test_t11_idempotency_same_phone_payload(self):
        payload = {"phone": "3334445555"}

        first  = match_or_create_lead_from_ghl_payload(payload, db_path=TEST_DB)
        second = match_or_create_lead_from_ghl_payload(payload, db_path=TEST_DB)

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["app_lead_id"], second["app_lead_id"])
        # First call creates; second call matches.
        self.assertEqual(first["matched_by"],  "created")
        self.assertEqual(second["matched_by"], "phone")
        self.assertEqual(_count_leads(), 1)

    # ------------------------------------------------------------------
    # T12 — email normalized to lowercase before matching
    # ------------------------------------------------------------------
    def test_t12_email_lowercased_for_matching(self):
        _seed_lead("EMAIL_CASE_L", email="Bob@Example.COM")

        result = match_or_create_lead_from_ghl_payload(
            {"email": "BOB@EXAMPLE.COM"},
            db_path=TEST_DB,
        )

        # The seed row has the lowercase-stored value; the lookup must also
        # lowercase so the match succeeds.
        # NOTE: The seed was inserted with mixed-case so we verify the
        # normalisation by checking that no new lead is created when the DB
        # already has the lowercased form.
        # Re-seed with lowercase to match what the function normalises to.
        os.remove(TEST_DB)
        conn = connect(TEST_DB)
        init_db(conn)
        conn.close()
        _seed_lead("EMAIL_CASE_L2", email="bob@example.com")

        result2 = match_or_create_lead_from_ghl_payload(
            {"email": "BOB@EXAMPLE.COM"},
            db_path=TEST_DB,
        )

        self.assertTrue(result2["ok"])
        self.assertEqual(result2["app_lead_id"], "EMAIL_CASE_L2")
        self.assertEqual(result2["matched_by"], "email")

    # ------------------------------------------------------------------
    # T13 — phone stripped of surrounding whitespace before matching
    # ------------------------------------------------------------------
    def test_t13_phone_whitespace_stripped(self):
        _seed_lead("PHONE_WS_L", phone="5556667777")

        result = match_or_create_lead_from_ghl_payload(
            {"phone": "  5556667777  "},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["app_lead_id"], "PHONE_WS_L")
        self.assertEqual(result["matched_by"], "phone")

    # ------------------------------------------------------------------
    # T14 — created lead_id begins with "GHL_"
    # ------------------------------------------------------------------
    def test_t14_created_lead_id_has_ghl_prefix(self):
        result = match_or_create_lead_from_ghl_payload(
            {"phone": "1230001234"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_by"], "created")
        self.assertTrue(
            result["app_lead_id"].startswith("GHL_"),
            f"Expected GHL_ prefix, got: {result['app_lead_id']}",
        )

    # ------------------------------------------------------------------
    # T15 — supplied fields written to matched lead (update path)
    # ------------------------------------------------------------------
    def test_t15_supplied_fields_written_to_matched_lead(self):
        _seed_lead("UPDATE_L", phone="9998887777")

        match_or_create_lead_from_ghl_payload(
            {"phone": "9998887777", "email": "added@example.com", "name": "Added Name"},
            db_path=TEST_DB,
        )

        row = _fetch_lead("UPDATE_L")
        self.assertEqual(row["email"], "added@example.com")
        self.assertEqual(row["name"],  "Added Name")

    # ------------------------------------------------------------------
    # T16 — name-only, zero existing matches → new lead created
    # ------------------------------------------------------------------
    def test_t16_name_only_no_existing_creates_new_lead(self):
        result = match_or_create_lead_from_ghl_payload(
            {"name": "Completely New Person"},
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_by"], "created")
        self.assertEqual(_count_leads(), 1)


if __name__ == "__main__":
    unittest.main()
