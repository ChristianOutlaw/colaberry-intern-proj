"""
tests/test_build_ghl_full_field_payload.py

Unit tests for execution/ghl/build_ghl_full_field_payload.py.

Fast, deterministic, no network calls.
Uses an isolated SQLite file (tmp/test_build_ghl_full_field_payload.db) per test.

Scenarios covered
-----------------
T1  — lead not found → ok=False, message contains lead id
T2  — now=None raises ValueError
T3  — full payload shape: all 22 canonical keys present on success
T4  — identity fields populated from leads table
T5  — null identity fields (phone/email/name absent) → None in payload, not placeholder
T6  — no invite → invite_ready=False, invite_status=None, course_link=None
T7  — invite generated (token, no sent_at) → invite_ready=True, invite_status="GENERATED"
T8  — invite sent (sent_at set) → invite_status="SENT"
T9  — course_started=False when no course_state row
T10 — course_started=True when started_at is set in course_state
T11 — completion_pct and current_section from course_state
T12 — can_compute_score=False when invite not sent
T13 — booking_ready=False for new lead with no progress
T14 — action fields present: intended_action, action_status, action_completed
T15 — determinism: same db state + same now → identical payload
T16 — ghl_contact_id stored in payload when present on lead
T17 — invite_generated_at populated from course_invites.generated_at; independent of sent_at
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                              # noqa: E402
from execution.ghl.build_ghl_full_field_payload import build_ghl_full_field_payload  # noqa: E402

TEST_DB = str(REPO_ROOT / "tmp" / "test_build_ghl_full_field_payload.db")

_NOW = "2026-03-27T12:00:00+00:00"
_BASE_URL = "http://test.portal"

# ---------------------------------------------------------------------------
# All 22 canonical field keys that must be present in every success payload
# ---------------------------------------------------------------------------
_REQUIRED_PAYLOAD_KEYS = {
    # Group A
    "app_lead_id", "ghl_contact_id", "phone", "email", "full_name", "course_link",
    # Group B
    "invite_ready", "invite_status", "invite_generated_at", "invite_sent_at", "invite_channel",
    # Group C
    "course_started", "completion_pct", "current_section", "last_activity_at",
    # Group D
    "can_compute_score", "final_label", "booking_ready",
    # Group E
    "intended_action", "action_status", "action_completed", "action_completed_at",
    "last_action_sent_at",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_lead(
    lead_id: str,
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
    ghl_contact_id: str | None = None,
):
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


def _seed_invite(
    lead_id: str,
    invite_id: str,
    token: str,
    sent_at: str | None = None,
    channel: str | None = None,
    generated_at: str | None = None,
):
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO course_invites
                (id, lead_id, token, sent_at, channel, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invite_id, lead_id, token, sent_at, channel, generated_at),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_course_state(
    lead_id: str,
    current_section: str | None = None,
    completion_pct: float | None = None,
    last_activity_at: str | None = None,
    started_at: str | None = None,
):
    conn = connect(TEST_DB)
    try:
        init_db(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO course_state
                (lead_id, course_id, current_section, completion_pct,
                 last_activity_at, started_at, updated_at)
            VALUES (?, 'FREE_INTRO_AI_V0', ?, ?, ?, ?, '2026-01-01T00:00:00+00:00')
            """,
            (lead_id, current_section, completion_pct, last_activity_at, started_at),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestBuildGhlFullFieldPayload(unittest.TestCase):

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    # ------------------------------------------------------------------
    # T1 — lead not found → ok=False
    # ------------------------------------------------------------------
    def test_t1_lead_not_found_returns_ok_false(self):
        result = build_ghl_full_field_payload(
            "DOES_NOT_EXIST",
            now=_NOW,
            db_path=TEST_DB,
        )

        self.assertFalse(result["ok"])
        self.assertIn("message", result)
        self.assertIn("DOES_NOT_EXIST", result["message"])

    # ------------------------------------------------------------------
    # T2 — now=None raises ValueError
    # ------------------------------------------------------------------
    def test_t2_now_none_raises_value_error(self):
        _seed_lead("L_T2", phone="5550000001")
        with self.assertRaises(ValueError):
            build_ghl_full_field_payload("L_T2", now=None, db_path=TEST_DB)

    # ------------------------------------------------------------------
    # T3 — full payload shape: all required keys present
    # ------------------------------------------------------------------
    def test_t3_full_payload_shape_has_all_keys(self):
        _seed_lead("L_T3", phone="5550000003")

        result = build_ghl_full_field_payload(
            "L_T3",
            now=_NOW,
            base_url=_BASE_URL,
            db_path=TEST_DB,
        )

        self.assertTrue(result["ok"])
        payload = result["payload"]
        for key in _REQUIRED_PAYLOAD_KEYS:
            self.assertIn(key, payload, f"Missing required key: {key!r}")

    # ------------------------------------------------------------------
    # T4 — identity fields populated from leads table
    # ------------------------------------------------------------------
    def test_t4_identity_fields_populated(self):
        _seed_lead("L_T4", phone="5551112222", email="t4@example.com",
                   name="Test Four", ghl_contact_id="GHL_T4")

        result = build_ghl_full_field_payload("L_T4", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertEqual(p["app_lead_id"],    "L_T4")
        self.assertEqual(p["phone"],          "5551112222")
        self.assertEqual(p["email"],          "t4@example.com")
        self.assertEqual(p["full_name"],      "Test Four")
        self.assertEqual(p["ghl_contact_id"], "GHL_T4")

    # ------------------------------------------------------------------
    # T5 — null identity fields → None in payload (not placeholder strings)
    # ------------------------------------------------------------------
    def test_t5_null_identity_fields_are_none(self):
        _seed_lead("L_T5")  # no phone, email, name, ghl_contact_id

        result = build_ghl_full_field_payload("L_T5", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertIsNone(p["phone"])
        self.assertIsNone(p["email"])
        self.assertIsNone(p["full_name"])
        self.assertIsNone(p["ghl_contact_id"])
        # Ensure no placeholder strings like "N/A", "NONE", or ""
        for field in ("phone", "email", "full_name", "ghl_contact_id"):
            self.assertNotEqual(p[field], "")
            self.assertNotEqual(p[field], "N/A")
            self.assertNotEqual(p[field], "NONE")

    # ------------------------------------------------------------------
    # T6 — no invite → invite_ready=False, invite_status=None, course_link=None
    # ------------------------------------------------------------------
    def test_t6_no_invite_fields_are_false_or_none(self):
        _seed_lead("L_T6", phone="5552223333")

        result = build_ghl_full_field_payload("L_T6", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertFalse(p["invite_ready"])
        self.assertIsNone(p["invite_status"])
        self.assertIsNone(p["course_link"])
        self.assertIsNone(p["invite_sent_at"])
        self.assertIsNone(p["invite_channel"])

    # ------------------------------------------------------------------
    # T7 — invite generated (token exists, no sent_at) → invite_ready=True, status=GENERATED
    # ------------------------------------------------------------------
    def test_t7_generated_invite_sets_invite_ready_and_status(self):
        _seed_lead("L_T7", phone="5553334444")
        _seed_invite("L_T7", "INV_T7", token="tok_abc123", sent_at=None)

        result = build_ghl_full_field_payload(
            "L_T7", now=_NOW, base_url=_BASE_URL, db_path=TEST_DB
        )

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertTrue(p["invite_ready"])
        self.assertEqual(p["invite_status"], "GENERATED")
        self.assertIsNotNone(p["course_link"])
        self.assertIn("tok_abc123", p["course_link"])

    # ------------------------------------------------------------------
    # T8 — invite sent (sent_at set) → invite_status="SENT"
    # ------------------------------------------------------------------
    def test_t8_sent_invite_sets_status_sent(self):
        _seed_lead("L_T8", phone="5554445555")
        _seed_invite(
            "L_T8", "INV_T8", token="tok_sent_xyz",
            sent_at="2026-03-01T10:00:00+00:00",
            channel="SMS",
        )

        result = build_ghl_full_field_payload(
            "L_T8", now=_NOW, base_url=_BASE_URL, db_path=TEST_DB
        )

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertTrue(p["invite_ready"])
        self.assertEqual(p["invite_status"],  "SENT")
        self.assertEqual(p["invite_sent_at"], "2026-03-01T10:00:00+00:00")
        self.assertEqual(p["invite_channel"], "SMS")

    # ------------------------------------------------------------------
    # T9 — no course_state row → course_started=False, progress fields None
    # ------------------------------------------------------------------
    def test_t9_no_course_state_defaults(self):
        _seed_lead("L_T9", phone="5555556666")

        result = build_ghl_full_field_payload("L_T9", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertFalse(p["course_started"])
        self.assertIsNone(p["completion_pct"])
        self.assertIsNone(p["current_section"])
        self.assertIsNone(p["last_activity_at"])

    # ------------------------------------------------------------------
    # T10 — started_at set in course_state → course_started=True
    # ------------------------------------------------------------------
    def test_t10_started_at_set_makes_course_started_true(self):
        _seed_lead("L_T10", phone="5556667777")
        _seed_course_state(
            "L_T10",
            started_at="2026-03-10T08:00:00+00:00",
            completion_pct=35.0,
        )

        result = build_ghl_full_field_payload("L_T10", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertTrue(p["course_started"])

    # ------------------------------------------------------------------
    # T11 — completion_pct and current_section from course_state
    # ------------------------------------------------------------------
    def test_t11_course_progress_fields_populated(self):
        _seed_lead("L_T11", phone="5557778888")
        _seed_course_state(
            "L_T11",
            current_section="section_2",
            completion_pct=50.0,
            last_activity_at="2026-03-20T09:00:00+00:00",
            started_at="2026-03-05T08:00:00+00:00",
        )

        result = build_ghl_full_field_payload("L_T11", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertEqual(p["completion_pct"],   50.0)
        self.assertEqual(p["current_section"],  "section_2")
        self.assertEqual(p["last_activity_at"], "2026-03-20T09:00:00+00:00")

    # ------------------------------------------------------------------
    # T12 — can_compute_score=False when invite not sent
    # ------------------------------------------------------------------
    def test_t12_can_compute_score_false_without_invite(self):
        _seed_lead("L_T12", phone="5558889999")

        result = build_ghl_full_field_payload("L_T12", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertFalse(p["can_compute_score"])
        self.assertIsNone(p["final_label"])

    # ------------------------------------------------------------------
    # T13 — booking_ready=False for new lead with no progress
    # ------------------------------------------------------------------
    def test_t13_booking_ready_false_for_new_lead(self):
        _seed_lead("L_T13", phone="5559990000")

        result = build_ghl_full_field_payload("L_T13", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        self.assertFalse(result["payload"]["booking_ready"])

    # ------------------------------------------------------------------
    # T14 — action fields present with correct types
    # ------------------------------------------------------------------
    def test_t14_action_fields_present_and_typed(self):
        _seed_lead("L_T14", phone="5550001111")

        result = build_ghl_full_field_payload("L_T14", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertIn("intended_action",     p)
        self.assertIn("action_status",       p)
        self.assertIn("action_completed",    p)
        self.assertIn("action_completed_at", p)
        self.assertIn("last_action_sent_at", p)
        # action_completed must be a bool (not None)
        self.assertIsInstance(p["action_completed"], bool)
        self.assertFalse(p["action_completed"])
        # action_status must be a string when set
        self.assertIsInstance(p["action_status"], str)

    # ------------------------------------------------------------------
    # T15 — determinism: same db state + same now → identical payload
    # ------------------------------------------------------------------
    def test_t15_same_state_same_now_same_payload(self):
        _seed_lead("L_T15", phone="5550002222", email="det@example.com")
        _seed_invite("L_T15", "INV_T15", token="tok_det", sent_at=None)
        _seed_course_state(
            "L_T15",
            current_section="section_1",
            completion_pct=20.0,
            last_activity_at="2026-03-25T10:00:00+00:00",
            started_at="2026-03-20T08:00:00+00:00",
        )

        first  = build_ghl_full_field_payload(
            "L_T15", now=_NOW, base_url=_BASE_URL, db_path=TEST_DB
        )
        second = build_ghl_full_field_payload(
            "L_T15", now=_NOW, base_url=_BASE_URL, db_path=TEST_DB
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["payload"], second["payload"])

    # ------------------------------------------------------------------
    # T16 — ghl_contact_id present on lead → appears in payload
    # ------------------------------------------------------------------
    def test_t16_ghl_contact_id_in_payload(self):
        _seed_lead("L_T16", phone="5550003333", ghl_contact_id="GHL_T16_ID")

        result = build_ghl_full_field_payload("L_T16", now=_NOW, db_path=TEST_DB)

        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["ghl_contact_id"], "GHL_T16_ID")


    # ------------------------------------------------------------------
    # T17 — invite_generated_at from course_invites.generated_at;
    #        independent of invite_sent_at
    # ------------------------------------------------------------------
    def test_t17_invite_generated_at_populated_and_independent_of_sent_at(self):
        _seed_lead("L_T17", phone="5550004444")
        _seed_invite(
            "L_T17", "INV_T17", token="tok_t17",
            generated_at="2026-03-01T09:00:00+00:00",
            sent_at="2026-03-01T10:00:00+00:00",
        )

        result = build_ghl_full_field_payload(
            "L_T17", now=_NOW, base_url=_BASE_URL, db_path=TEST_DB
        )

        self.assertTrue(result["ok"])
        p = result["payload"]
        self.assertEqual(p["invite_generated_at"], "2026-03-01T09:00:00+00:00")
        self.assertEqual(p["invite_sent_at"],      "2026-03-01T10:00:00+00:00")
        self.assertNotEqual(p["invite_generated_at"], p["invite_sent_at"])


if __name__ == "__main__":
    unittest.main()
