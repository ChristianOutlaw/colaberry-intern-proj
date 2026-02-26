"""
tests/test_reflection_responses.py

Unit tests for:
  execution/reflection/save_reflection_response.py
  execution/reflection/load_reflection_responses.py

Uses an isolated on-disk database (tmp/test_reflection.db) that is
created fresh before each test and removed afterward.  Never touches
the application database (tmp/app.db).

No network calls. No randomness. No datetime.now() in any tested code.
"""

import os
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db                                    # noqa: E402
from execution.reflection.save_reflection_response import (                         # noqa: E402
    save_reflection_response,
    _validate,
)
from execution.reflection.load_reflection_responses import load_reflection_responses  # noqa: E402

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_reflection.db")


# ---------------------------------------------------------------------------
# Shared setUp / tearDown mixin
# ---------------------------------------------------------------------------

class _ReflectionTestBase(unittest.TestCase):
    """Opens a fresh, schema-initialised test database before each test."""

    def setUp(self):
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------

class TestReflectionTableCreated(_ReflectionTestBase):

    def test_reflection_responses_table_exists_after_init(self):
        """init_db() must create the reflection_responses table."""
        conn = connect(TEST_DB_PATH)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        self.assertIn("reflection_responses", tables)

    def test_reflection_responses_columns(self):
        """reflection_responses must have the expected columns."""
        conn = connect(TEST_DB_PATH)
        try:
            info = conn.execute("PRAGMA table_info(reflection_responses)").fetchall()
        finally:
            conn.close()

        col_names = {row[1] for row in info}
        expected = {"id", "lead_id", "course_id", "section_id",
                    "prompt_index", "response_text", "created_at"}
        self.assertEqual(col_names, expected)


# ---------------------------------------------------------------------------
# 2. Happy path — save then load
# ---------------------------------------------------------------------------

class TestSaveAndLoad(_ReflectionTestBase):

    def test_save_then_load_single_response(self):
        """Saving one response and loading it returns the correct mapping."""
        save_reflection_response(
            "L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "AI is fascinating",
            db_path=TEST_DB_PATH,
        )
        result = load_reflection_responses("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)

        self.assertEqual(result, {"P1_S1": {0: "AI is fascinating"}})

    def test_save_then_load_multiple_sections_and_prompts(self):
        """Responses across sections and prompt slots are grouped correctly."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "Answer A", db_path=TEST_DB_PATH)
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 1, "Answer B", db_path=TEST_DB_PATH)
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P2_S3", 0, "Answer C", db_path=TEST_DB_PATH)

        result = load_reflection_responses("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)

        self.assertEqual(result, {
            "P1_S1": {0: "Answer A", 1: "Answer B"},
            "P2_S3": {0: "Answer C"},
        })

    def test_load_returns_empty_dict_for_unknown_lead(self):
        """Loading for a lead with no responses returns an empty dict."""
        result = load_reflection_responses("UNKNOWN", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        self.assertEqual(result, {})

    def test_load_returns_empty_dict_for_unknown_course(self):
        """Loading for a known lead but different course returns an empty dict."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        result = load_reflection_responses("L1", "OTHER_COURSE", db_path=TEST_DB_PATH)
        self.assertEqual(result, {})

    def test_responses_isolated_by_lead(self):
        """Responses for L1 do not appear when loading for L2."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "L1 answer", db_path=TEST_DB_PATH)
        save_reflection_response("L2", "FREE_INTRO_AI_V0", "P1_S1", 0, "L2 answer", db_path=TEST_DB_PATH)

        r1 = load_reflection_responses("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        r2 = load_reflection_responses("L2", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)

        self.assertEqual(r1["P1_S1"][0], "L1 answer")
        self.assertEqual(r2["P1_S1"][0], "L2 answer")

    def test_created_at_stored_when_provided(self):
        """created_at value is persisted and retrievable via raw SQL."""
        ts = "2026-02-25T12:00:00+00:00"
        save_reflection_response(
            "L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "My answer",
            created_at=ts,
            db_path=TEST_DB_PATH,
        )
        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT created_at FROM reflection_responses WHERE lead_id=? AND section_id=?",
                ("L1", "P1_S1"),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["created_at"], ts)

    def test_created_at_null_when_omitted(self):
        """created_at is stored as NULL when not supplied."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        try:
            row = conn.execute(
                "SELECT created_at FROM reflection_responses WHERE lead_id=?", ("L1",)
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row["created_at"])


# ---------------------------------------------------------------------------
# 3. Upsert behaviour
# ---------------------------------------------------------------------------

class TestUpsertBehaviour(_ReflectionTestBase):

    def test_second_save_overwrites_first(self):
        """Saving again for the same slot replaces the previous response."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "original", db_path=TEST_DB_PATH)
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "updated", db_path=TEST_DB_PATH)

        result = load_reflection_responses("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        self.assertEqual(result["P1_S1"][0], "updated")

    def test_upsert_does_not_create_duplicate_rows(self):
        """After two saves for the same slot there is exactly one row in the DB."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "v1", db_path=TEST_DB_PATH)
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "v2", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM reflection_responses WHERE lead_id=? AND section_id=? AND prompt_index=?",
                ("L1", "P1_S1", 0),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_different_prompt_index_creates_separate_rows(self):
        """Saves to prompt_index=0 and prompt_index=1 produce two distinct rows."""
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 0, "first", db_path=TEST_DB_PATH)
        save_reflection_response("L1", "FREE_INTRO_AI_V0", "P1_S1", 1, "second", db_path=TEST_DB_PATH)

        result = load_reflection_responses("L1", "FREE_INTRO_AI_V0", db_path=TEST_DB_PATH)
        self.assertEqual(result["P1_S1"], {0: "first", 1: "second"})


# ---------------------------------------------------------------------------
# 4. Validation errors — save_reflection_response
# ---------------------------------------------------------------------------

class TestSaveValidationErrors(_ReflectionTestBase):

    # --- string fields ---------------------------------------------------

    def test_empty_lead_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("", "COURSE", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        self.assertIn("lead_id", str(ctx.exception))

    def test_whitespace_lead_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("   ", "COURSE", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        self.assertIn("lead_id", str(ctx.exception))

    def test_non_string_lead_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response(None, "COURSE", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        self.assertIn("lead_id", str(ctx.exception))

    def test_empty_course_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "", "P1_S1", 0, "x", db_path=TEST_DB_PATH)
        self.assertIn("course_id", str(ctx.exception))

    def test_empty_section_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "", 0, "x", db_path=TEST_DB_PATH)
        self.assertIn("section_id", str(ctx.exception))

    def test_empty_response_text_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", 0, "", db_path=TEST_DB_PATH)
        self.assertIn("response_text", str(ctx.exception))

    def test_whitespace_response_text_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", 0, "  \t  ", db_path=TEST_DB_PATH)
        self.assertIn("response_text", str(ctx.exception))

    # --- prompt_index ----------------------------------------------------

    def test_prompt_index_string_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", "0", "x", db_path=TEST_DB_PATH)
        self.assertIn("prompt_index", str(ctx.exception))

    def test_prompt_index_float_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", 0.0, "x", db_path=TEST_DB_PATH)
        self.assertIn("prompt_index", str(ctx.exception))

    def test_prompt_index_bool_raises(self):
        """True is a bool and must be rejected even though bool subclasses int."""
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", True, "x", db_path=TEST_DB_PATH)
        self.assertIn("prompt_index", str(ctx.exception))

    def test_prompt_index_negative_raises(self):
        with self.assertRaises(ValueError) as ctx:
            save_reflection_response("L1", "COURSE", "P1_S1", -1, "x", db_path=TEST_DB_PATH)
        self.assertIn("prompt_index", str(ctx.exception))

    def test_validation_does_not_write_on_error(self):
        """A ValueError from validation must leave the DB unchanged."""
        with self.assertRaises(ValueError):
            save_reflection_response("", "COURSE", "P1_S1", 0, "x", db_path=TEST_DB_PATH)

        conn = connect(TEST_DB_PATH)
        try:
            count = conn.execute("SELECT COUNT(*) FROM reflection_responses").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# 5. Validation errors — load_reflection_responses
# ---------------------------------------------------------------------------

class TestLoadValidationErrors(_ReflectionTestBase):

    def test_empty_lead_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load_reflection_responses("", "COURSE", db_path=TEST_DB_PATH)
        self.assertIn("lead_id", str(ctx.exception))

    def test_none_lead_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load_reflection_responses(None, "COURSE", db_path=TEST_DB_PATH)
        self.assertIn("lead_id", str(ctx.exception))

    def test_empty_course_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load_reflection_responses("L1", "", db_path=TEST_DB_PATH)
        self.assertIn("course_id", str(ctx.exception))

    def test_none_course_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            load_reflection_responses("L1", None, db_path=TEST_DB_PATH)
        self.assertIn("course_id", str(ctx.exception))


# ---------------------------------------------------------------------------
# 6. _validate helper (no DB required)
# ---------------------------------------------------------------------------

class TestValidateHelper(unittest.TestCase):
    """Tests the _validate function directly — no DB setup needed."""

    def test_valid_args_pass(self):
        """_validate should not raise for valid inputs."""
        _validate("L1", "COURSE", "P1_S1", 0, "Some text")  # must not raise

    def test_prompt_index_zero_is_valid(self):
        _validate("L1", "C", "S", 0, "x")

    def test_prompt_index_large_is_valid(self):
        _validate("L1", "C", "S", 999, "x")


if __name__ == "__main__":
    unittest.main()
