"""
tests/test_load_course_map.py

Unit tests for execution/course/load_course_map.py.

Three test groups:
  1. Happy path — real FREE_INTRO_AI_V0 course_map.json loads correctly.
  2. Invalid course_id — ValueError for unknown course IDs.
  3. Validation errors — synthetic bad structures trigger descriptive ValueErrors.

No database access. No randomness. No network calls.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.course.load_course_map import (  # noqa: E402
    load_course_map,
    _build_and_validate,
)
from execution.course.course_registry import TOTAL_SECTIONS, SECTION_IDS  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestLoadCourseMapHappyPath(unittest.TestCase):

    def test_load_course_map_happy_path(self):
        """Returns dict keyed by section_id with all expected sections."""
        result = load_course_map("FREE_INTRO_AI_V0")

        self.assertIsInstance(result, dict)

        # Must match the canonical section count from the registry.
        self.assertEqual(
            len(result),
            TOTAL_SECTIONS,
            f"Expected {TOTAL_SECTIONS} sections, got {len(result)}",
        )

        # Keys must exactly match the registry's canonical section IDs.
        self.assertEqual(
            set(result.keys()),
            set(SECTION_IDS),
            "Loaded section IDs do not match course_registry.SECTION_IDS",
        )

        # Every value must be a dict (the raw section object).
        for section_id, section_data in result.items():
            with self.subTest(section_id=section_id):
                self.assertIsInstance(section_data, dict)

    def test_known_section_data_integrity(self):
        """Spot-check P1_S1 has the expected title and non-empty quiz_ids."""
        result = load_course_map("FREE_INTRO_AI_V0")
        p1_s1 = result["P1_S1"]
        self.assertEqual(p1_s1.get("title"), "What Is AI?")
        self.assertIsInstance(p1_s1.get("quiz_ids"), list)
        self.assertGreater(len(p1_s1["quiz_ids"]), 0)


# ---------------------------------------------------------------------------
# 2. Invalid course_id
# ---------------------------------------------------------------------------

class TestLoadCourseMapInvalidCourseId(unittest.TestCase):

    def test_load_course_map_invalid_course_id(self):
        """Raises ValueError for unknown course_id; message names the bad ID."""
        with self.assertRaises(ValueError) as ctx:
            load_course_map("NONEXISTENT_COURSE")
        self.assertIn("NONEXISTENT_COURSE", str(ctx.exception))

    def test_load_course_map_empty_string(self):
        """Raises ValueError for empty-string course_id."""
        with self.assertRaises(ValueError):
            load_course_map("")

    def test_load_course_map_wrong_case(self):
        """Raises ValueError for course_id with wrong casing."""
        with self.assertRaises(ValueError):
            load_course_map("free_intro_ai_v0")


# ---------------------------------------------------------------------------
# 3. Validation errors (synthetic bad structures via _build_and_validate)
# ---------------------------------------------------------------------------

class TestLoadCourseMapValidationErrors(unittest.TestCase):
    """
    Tests the validation logic using synthetic JSON, avoiding file I/O.
    _build_and_validate is tested directly so production code stays simple.
    """

    def test_top_level_not_dict_raises(self):
        """ValueError when top-level JSON is not a dict."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(["not", "a", "dict"], "TEST_COURSE")
        self.assertIn("top-level must be a dict", str(ctx.exception))

    def test_top_level_none_raises(self):
        """ValueError when top-level JSON is None."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(None, "TEST_COURSE")
        self.assertIn("top-level must be a dict", str(ctx.exception))

    def test_missing_sections_key_raises(self):
        """ValueError when 'sections' key is absent."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate({"schema_version": "1.0"}, "TEST_COURSE")
        self.assertIn("sections", str(ctx.exception))

    def test_sections_not_list_raises(self):
        """ValueError when 'sections' is a dict instead of a list."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate({"sections": {"P1_S1": {}}}, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("sections", msg)
        self.assertIn("list", msg)

    def test_section_entry_not_dict_raises(self):
        """ValueError when a section entry is a string instead of a dict."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate({"sections": ["not_a_dict"]}, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("dict", msg)

    def test_section_missing_section_id_raises(self):
        """ValueError when a section dict is missing 'section_id'."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate({"sections": [{"title": "No ID here"}]}, "TEST_COURSE")
        self.assertIn("section_id", str(ctx.exception))

    def test_section_id_empty_string_raises(self):
        """ValueError when section_id is an empty string."""
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate({"sections": [{"section_id": ""}]}, "TEST_COURSE")
        self.assertIn("section_id", str(ctx.exception))

    def test_quiz_ids_not_list_raises(self):
        """ValueError when quiz_ids is a string; message names the field."""
        bad = {"sections": [{"section_id": "P1_S1", "quiz_ids": "not_a_list"}]}
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(bad, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("quiz_ids", msg)
        self.assertIn("list", msg)

    def test_quiz_ids_non_string_element_raises(self):
        """ValueError when quiz_ids contains a non-string element."""
        bad = {"sections": [{"section_id": "P1_S1", "quiz_ids": [1, 2, 3]}]}
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(bad, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("quiz_ids", msg)

    def test_reflection_prompts_not_list_raises(self):
        """ValueError when reflection_prompts is an int; message names the field."""
        bad = {"sections": [{"section_id": "P1_S1", "reflection_prompts": 42}]}
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(bad, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("reflection_prompts", msg)
        self.assertIn("list", msg)

    def test_reflection_prompts_non_string_element_raises(self):
        """ValueError when reflection_prompts contains a non-string element."""
        bad = {"sections": [{"section_id": "P1_S1", "reflection_prompts": [None]}]}
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(bad, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("reflection_prompts", msg)

    def test_curriculum_refs_not_list_raises(self):
        """ValueError when curriculum_refs is a string instead of a list."""
        bad = {"sections": [{"section_id": "P1_S1", "curriculum_refs": "bad"}]}
        with self.assertRaises(ValueError) as ctx:
            _build_and_validate(bad, "TEST_COURSE")
        msg = str(ctx.exception)
        self.assertIn("curriculum_refs", msg)
        self.assertIn("list", msg)

    def test_valid_minimal_structure_passes(self):
        """A minimal valid structure with one section returns successfully."""
        valid = {
            "sections": [
                {
                    "section_id": "P1_S1",
                    "quiz_ids": ["quiz_1"],
                    "reflection_prompts": ["prompt_1"],
                    "curriculum_refs": [{"phase_id": "phase1"}],
                    "extra_key": "allowed",
                }
            ]
        }
        result = _build_and_validate(valid, "TEST_COURSE")
        self.assertIn("P1_S1", result)
        self.assertIsInstance(result["P1_S1"], dict)

    def test_sections_with_all_optional_fields_absent_passes(self):
        """Sections with no optional fields (quiz_ids etc.) are valid."""
        valid = {
            "sections": [
                {"section_id": "P1_S1", "title": "Just a title"},
                {"section_id": "P1_S2"},
            ]
        }
        result = _build_and_validate(valid, "TEST_COURSE")
        self.assertEqual(set(result.keys()), {"P1_S1", "P1_S2"})


# ---------------------------------------------------------------------------
# 4. FileNotFoundError via _REPO_ROOT monkeypatch
# ---------------------------------------------------------------------------

class TestLoadCourseMapFileNotFound(unittest.TestCase):

    def test_file_not_found_for_missing_course_content(self):
        """FileNotFoundError when course_map.json is absent from the expected path."""
        import execution.course.load_course_map as mod

        original_root = mod._REPO_ROOT
        try:
            with tempfile.TemporaryDirectory() as td:
                mod._REPO_ROOT = Path(td)
                with self.assertRaises(FileNotFoundError):
                    load_course_map("FREE_INTRO_AI_V0")
        finally:
            mod._REPO_ROOT = original_root


if __name__ == "__main__":
    unittest.main()
